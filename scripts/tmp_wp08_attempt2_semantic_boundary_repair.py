#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import py_compile
import subprocess

GOAL_PATH = Path("services/agent-service/src/agent_core/lifecycle/goal_planning.py")
PROTOCOL_PATH = Path("services/agent-service/src/agent_core/lifecycle/protocol.py")
NEW_TEST = Path("skill-system/tests/test_wp08_attempt2_semantic_boundary_repair.py")
BASELINE_PATH = Path("skill-system/registry/product-source-baseline.json")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_goal_planning(root: Path) -> None:
    path = root / GOAL_PATH
    text = read(path)

    helper_anchor = '''    return rows\n\n\nclass ModelGoalAlignmentVerifier:\n'''
    helpers = '''    return rows\n\n\ndef _requested_effect_identity_key(goal: dict[str, Any]) -> tuple[str, str, str]:\n    effect = goal.get("requested_effect") if isinstance(goal.get("requested_effect"), dict) else {}\n    return tuple(\n        _clean_text(effect.get(field), limit=160).casefold()\n        for field in ("domain", "operation", "object_type")\n    )\n\n\ndef _requested_effect_reaudit_collision_guard(\n    goals: list[dict[str, Any]],\n    missing_spans: tuple[str, ...],\n) -> dict[str, Any]:\n    """Fail closed on a structurally ambiguous sibling-effect collapse.\n\n    The semantic verifier still owns language meaning. Runtime does not decide\n    whether an effect is supported or inspect the capability registry. This\n    guard activates only after the independent candidate-blind verifier has\n    already reported a requested-effect mismatch. If the disputed Goal shares\n    the exact same structured effect identity with a different sibling Goal, a\n    later verifier call is not allowed to erase that mismatch as mere naming\n    granularity without a fresh declaration.\n    """\n    missing = tuple(_clean_text(value, limit=240) for value in missing_spans if _clean_text(value, limit=240))\n    disputed_ids: set[str] = set()\n    for goal in goals:\n        evidence = _clean_text(goal.get("evidence_span"), limit=240)\n        if evidence and any(span in evidence or evidence in span for span in missing):\n            disputed_ids.add(_clean_text(goal.get("goal_id"), limit=80))\n    by_identity: dict[tuple[str, str, str], list[dict[str, Any]]] = {}\n    for goal in goals:\n        identity = _requested_effect_identity_key(goal)\n        if all(identity):\n            by_identity.setdefault(identity, []).append(goal)\n    collisions: list[dict[str, Any]] = []\n    for identity, rows in by_identity.items():\n        ids = {_clean_text(row.get("goal_id"), limit=80) for row in rows}\n        if len(ids) < 2 or not ids.intersection(disputed_ids):\n            continue\n        evidence = {_clean_text(row.get("evidence_span"), limit=240) for row in rows}\n        if len({value for value in evidence if value}) < 2:\n            continue\n        collisions.append({\n            "effect_identity": {\n                "domain": identity[0],\n                "operation": identity[1],\n                "object_type": identity[2],\n            },\n            "goal_ids": sorted(ids),\n        })\n    return {\n        "risk": bool(collisions),\n        "missing_spans": list(missing),\n        "disputed_goal_ids": sorted(disputed_ids),\n        "collisions": collisions,\n        "capability_registry_consulted": False,\n        "language_interpretation_used": False,\n    }\n\n\ndef _literal_role_overlap(left: str, right: str) -> bool:\n    left_key = "".join(str(left or "").split()).casefold()\n    right_key = "".join(str(right or "").split()).casefold()\n    return bool(left_key and right_key and (left_key in right_key or right_key in left_key))\n\n\ndef _scope_constraint_role_conflict_errors(\n    goals: list[dict[str, Any]],\n    *,\n    user_text: str,\n) -> list[str]:\n    """Reject one literal span being assigned incompatible semantic roles.\n\n    This is a structural invariant only. It does not classify pronouns, filters\n    or business vocabulary. A historical reference span and a literal execution\n    commitment are already explicitly typed by the Planner; neither may also be\n    frozen as a population-narrowing scope constraint.\n    """\n    errors: list[str] = []\n    for goal in goals:\n        goal_id = _clean_text(goal.get("goal_id"), limit=80) or "missing"\n        target = goal.get("target_candidate") if isinstance(goal.get("target_candidate"), dict) else {}\n        scope_spans = [\n            _clean_text(row.get("evidence_span"), limit=240)\n            for row in list(target.get("scope_constraints") or [])\n            if isinstance(row, dict) and _clean_text(row.get("evidence_span"), limit=240)\n        ]\n        reference = goal.get("reference_expression") if isinstance(goal.get("reference_expression"), dict) else {}\n        reference_span = _clean_text(reference.get("evidence_span"), limit=240)\n        commitment = _clean_text(goal.get("execution_commitment"), limit=240)\n        literal_commitment = commitment if commitment and commitment in user_text else ""\n        for index, span in enumerate(scope_spans):\n            if reference_span and _literal_role_overlap(span, reference_span):\n                errors.append(f"scope_constraint_conflicts_with_reference_expression:{goal_id}:{index}")\n            if literal_commitment and _literal_role_overlap(span, literal_commitment):\n                errors.append(f"scope_constraint_conflicts_with_execution_commitment:{goal_id}:{index}")\n    return errors\n\n\nclass ModelGoalAlignmentVerifier:\n'''
    text = replace_once(text, helper_anchor, helpers, label="semantic guard helpers")

    old_blind = '''            "Mere object/topic/member naming is target identity, not automatically a scope constraint. Goal.condition is a "\n            "separate condition/dependency algebra and ordinary target-population filtering must not be forced into it. Do not "\n            "invent a missing target member, slot/form value, current business fact or execution-time cardinality. If "\n            "requested_effect is semantically substituted, or an explicit narrowing predicate is absent from scope_constraints, "\n            "verdict must be incomplete and missing_spans must copy the smallest literal USER_TEXT span that proves the mismatch. "\n'''
    new_blind = '''            "Mere object/topic/member naming is target identity, not automatically a scope constraint. Goal.condition is a "\n            "separate condition/dependency algebra and ordinary target-population filtering must not be forced into it. Audit the "\n            "inverse direction too: every supplied scope_constraints entry must itself be a real population-narrowing predicate. "\n            "A historical-result/member reference, execution commitment, input/control wording or ordinary target identity must not "\n            "be stored as a scope constraint. If a supplied scope constraint has one of those other semantic roles, verdict must be "\n            "incomplete with a target-scope-constraint fidelity reason. Do not invent a missing target member, slot/form value, current "\n            "business fact or execution-time cardinality. If requested_effect is semantically substituted, an explicit narrowing "\n            "predicate is absent from scope_constraints, or scope_constraints overstates a non-narrowing phrase, verdict must be "\n            "incomplete and missing_spans must copy the smallest literal USER_TEXT span that proves the mismatch. "\n'''
    text = replace_once(text, old_blind, new_blind, label="bidirectional blind scope audit")

    old_rules = '''            "target-member selection, unprovided form values and current business facts are downstream Runtime concerns and are not missing scope constraints",\n            "judge semantic result dependency independently from execution-support dataflow",\n'''
    new_rules = '''            "target-member selection, historical-result/member reference, execution commitment, input/control wording, unprovided form values and current business facts are not scope constraints; if one is explicitly placed in scope_constraints return incomplete instead of letting Runtime bind it as a filter",\n            "judge semantic result dependency independently from execution-support dataflow",\n'''
    text = replace_once(text, old_rules, new_rules, label="blind scope overreach rule")

    old_locals = '''        initial_exact_alignment: GoalAlignmentVerdict | None = None\n        for attempt in range(3):\n'''
    new_locals = '''        initial_exact_alignment: GoalAlignmentVerdict | None = None\n        requested_effect_reaudit_guard: dict[str, Any] | None = None\n        for attempt in range(3):\n'''
    text = replace_once(text, old_locals, new_locals, label="reaudit guard state")

    old_exact = '''                    if (\n                        blind_dependency_audit\n                        and raw_verdict == "exact"\n                        and initial_exact_alignment is not None\n                    ):\n                        # The second verifier is an independent semantic-contract audit:\n                        # dependency graph plus requested-effect/target-scope fidelity.\n                        # Outcome grounding was already proven by the first exact\n                        # call, so preserve that literal evidence while accepting\n                        # only a structurally valid candidate-blind audit result.\n                        verdict = GoalAlignmentVerdict(\n                            "exact",\n                            initial_exact_alignment.evidence_spans,\n                            (),\n                            "goal_alignment_candidate_blind_dependency_reaudit_exact",\n                            "model",\n                            True,\n                            {\n                                **initial_exact_alignment.details,\n                                "initial_alignment_reason_code": initial_exact_alignment.reason_code,\n                                "candidate_blind_dependency_reaudit": True,\n                            },\n                        )\n'''
    new_exact = '''                    if (\n                        blind_dependency_audit\n                        and raw_verdict == "exact"\n                        and initial_exact_alignment is not None\n                    ):\n                        # The second verifier is an independent semantic-contract audit:\n                        # dependency graph plus requested-effect/target-scope fidelity.\n                        # Outcome grounding was already proven by the first exact\n                        # call, so preserve that literal evidence while accepting\n                        # only a structurally valid candidate-blind audit result.\n                        if (\n                            verifier_repair_kind == "candidate_blind_dependency_requested_effect_reaudit"\n                            and isinstance(requested_effect_reaudit_guard, dict)\n                            and requested_effect_reaudit_guard.get("risk") is True\n                        ):\n                            # A verifier disagreement cannot silently collapse two\n                            # independently declared sibling outcomes onto the same\n                            # structured effect identity. This guard is structural\n                            # only and does not inspect capability availability.\n                            verdict = GoalAlignmentVerdict(\n                                "incomplete",\n                                initial_exact_alignment.evidence_spans,\n                                tuple(requested_effect_reaudit_guard.get("missing_spans") or ()),\n                                "requested_effect_reaudit_structural_collision",\n                                "model",\n                                True,\n                                {\n                                    **initial_exact_alignment.details,\n                                    "initial_alignment_reason_code": initial_exact_alignment.reason_code,\n                                    "candidate_blind_dependency_reaudit": True,\n                                    "requested_effect_reaudit_guard": dict(requested_effect_reaudit_guard),\n                                },\n                            )\n                        else:\n                            verdict = GoalAlignmentVerdict(\n                                "exact",\n                                initial_exact_alignment.evidence_spans,\n                                (),\n                                "goal_alignment_candidate_blind_dependency_reaudit_exact",\n                                "model",\n                                True,\n                                {\n                                    **initial_exact_alignment.details,\n                                    "initial_alignment_reason_code": initial_exact_alignment.reason_code,\n                                    "candidate_blind_dependency_reaudit": True,\n                                },\n                            )\n'''
    text = replace_once(text, old_exact, new_exact, label="requested effect collision fail closed")

    old_trigger = '''                # Candidate-blind requested-effect audit is intentionally strict,\n                # but an open/unsupported effect has no registered capability\n                # identity to copy. Spend the already-budgeted third verifier call\n                # on the semantic mismatch claim itself instead of treating naming\n                # granularity as product evidence. Runtime still never chooses a\n                # capability or rewrites the requested effect.\n                verifier_repair_kind = "candidate_blind_dependency_requested_effect_reaudit"\n'''
    new_trigger = '''                # Candidate-blind requested-effect audit is intentionally strict,\n                # but an open/unsupported effect has no registered capability\n                # identity to copy. Spend the already-budgeted third verifier call\n                # on the semantic mismatch claim itself instead of treating naming\n                # granularity as product evidence. Runtime still never chooses a\n                # capability or rewrites the requested effect.\n                requested_effect_reaudit_guard = _requested_effect_reaudit_collision_guard(\n                    goals, verdict.missing_spans\n                )\n                verifier_repair_kind = "candidate_blind_dependency_requested_effect_reaudit"\n'''
    text = replace_once(text, old_trigger, new_trigger, label="capture requested effect collision guard")

    old_reaudit_prompt = '''                    "If it substitutes a different lookup, action, object or business effect, remain incomplete and copy only "\n                    "the smallest literal USER_TEXT span proving that substitution into missing_spans. Do not choose a tool, "\n'''
    new_reaudit_prompt = '''                    "If it substitutes a different lookup, action, object or business effect, remain incomplete and copy only "\n                    "the smallest literal USER_TEXT span proving that substitution into missing_spans. If the disputed Goal uses "\n                    "the exact same structured domain/operation/object_type as a sibling Goal with a distinct independently requested "\n                    "outcome, do not erase the mismatch merely because raw_description is broad enough to sound compatible; that is a "\n                    "high-risk effect-collapse signal and requires a faithful fresh declaration. Do not choose a tool, "\n'''
    text = replace_once(text, old_reaudit_prompt, new_reaudit_prompt, label="requested effect collision re-audit instruction")

    old_reference_tail = '''            except ValueError as exc:\n                errors.append(f"invalid_reference_expression:{row['goal_id']}:{exc}")\n    for row in goals:\n        invalid = [dep for dep in row["depends_on"] if dep not in ids or dep == row["goal_id"]]\n'''
    new_reference_tail = '''            except ValueError as exc:\n                errors.append(f"invalid_reference_expression:{row['goal_id']}:{exc}")\n    errors.extend(_scope_constraint_role_conflict_errors(goals, user_text=user_text))\n    for row in goals:\n        invalid = [dep for dep in row["depends_on"] if dep not in ids or dep == row["goal_id"]]\n'''
    text = replace_once(text, old_reference_tail, new_reference_tail, label="scope role conflict validation")

    write(path, text)


def patch_protocol(root: Path) -> None:
    path = root / PROTOCOL_PATH
    text = read(path)
    old = '''        "开放的目标候选，不是业务事实。若当前 Goal 含有明确缩小目标/结果人口的筛选、状态、阈值或比较谓词，"\n        "必须把最小的当前原文字面证据写入 scope_constraints[].evidence_span。这里只冻结用户表达过的范围证据，"\n        "不得在此猜测归一化业务值；普通目标集合筛选也不得为了结构化而伪装成 Goal.condition。"\n'''
    new = '''        "开放的目标候选，不是业务事实。若当前 Goal 含有明确缩小目标/结果人口的筛选、状态、阈值或比较谓词，"\n        "必须把最小的当前原文字面证据写入 scope_constraints[].evidence_span。这里只冻结用户表达过的范围证据，"\n        "不得在此猜测归一化业务值；普通目标集合筛选也不得为了结构化而伪装成 Goal.condition。历史结果/成员引用"\n        "必须只进入 reference_expression；不要提交/只查询等执行承诺、输入或控制措辞也不是人口筛选，禁止复制到"\n        "scope_constraints。一个字面片段不得同时充当 reference_expression 与 scope_constraints。"\n'''
    text = replace_once(text, old, new, label="target candidate role description")
    old_exec = '''                            "execution_commitment": {"type": "string"},\n'''
    new_exec = '''                            "execution_commitment": {\n                                "type": "string",\n                                "description": "用户对执行方式/是否提交的承诺或限制；它不是 target_candidate.scope_constraints，也不能用来缩小业务对象人口。",\n                            },\n'''
    text = replace_once(text, old_exec, new_exec, label="execution commitment description")
    write(path, text)


def add_tests(root: Path) -> None:
    path = root / NEW_TEST
    if path.exists():
        raise SystemExit(f"test path already exists: {NEW_TEST}")
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


def test_requested_effect_reaudit_cannot_erase_sibling_effect_collision() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "Fetch the account status, then provide the private handler contact"
    effect = {"domain": "record", "operation": "query_status", "object_type": "record"}
    goals = [
        _goal("g1", "Fetch the account status", effect),
        _goal("g2", "provide the private handler contact", effect),
    ]
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["Fetch the account status", "provide the private handler contact"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "candidate_aware_exact",
    })
    mismatch = _response({
        "verdict": "incomplete",
        "evidence_spans": ["Fetch the account status", "provide the private handler contact"],
        "missing_spans": ["private handler contact"],
        "dependency_decisions": _pair(),
        "reason_code": "requested_effect_not_faithful_to_business_effect",
    })
    unsafe_withdrawal = _response({
        "verdict": "exact",
        "evidence_spans": [],
        "missing_spans": [],
        "dependency_decisions": _pair(),
        "reason_code": "naming_granularity_only",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, mismatch, unsafe_withdrawal],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=goals,
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "incomplete"
    assert verdict.reason_code == "requested_effect_reaudit_structural_collision"
    assert verdict.missing_spans == ("private handler contact",)
    guard = verdict.details["requested_effect_reaudit_guard"]
    assert guard["risk"] is True
    assert guard["capability_registry_consulted"] is False
    assert guard["language_interpretation_used"] is False
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_requested_effect_reaudit"


def test_requested_effect_collision_guard_is_inactive_for_unique_open_identity() -> None:
    from agent_core.lifecycle.goal_planning import _requested_effect_reaudit_collision_guard

    goals = [
        _goal("g1", "Fetch the account status", {"domain": "record", "operation": "query_status", "object_type": "record"}),
        _goal("g2", "provide the private handler contact", {"domain": "support", "operation": "get_handler_contact", "object_type": "handler"}),
    ]
    guard = _requested_effect_reaudit_collision_guard(goals, ("private handler contact",))
    assert guard["risk"] is False
    assert guard["collisions"] == []


def test_reference_span_cannot_also_be_scope_constraint() -> None:
    from agent_core.lifecycle.goal_planning import _scope_constraint_role_conflict_errors

    goals = [{
        "goal_id": "g1",
        "target_candidate": {"scope_constraints": [{"evidence_span": "that result"}]},
        "reference_expression": {"evidence_span": "that result"},
    }]
    errors = _scope_constraint_role_conflict_errors(goals, user_text="Can that result be refunded?")
    assert errors == ["scope_constraint_conflicts_with_reference_expression:g1:0"]


def test_literal_execution_commitment_cannot_also_be_scope_constraint() -> None:
    from agent_core.lifecycle.goal_planning import _scope_constraint_role_conflict_errors

    goals = [{
        "goal_id": "g1",
        "target_candidate": {"scope_constraints": [{"evidence_span": "do not submit"}]},
        "execution_commitment": "do not submit",
    }]
    errors = _scope_constraint_role_conflict_errors(
        goals,
        user_text="Check eligibility but do not submit anything",
    )
    assert errors == ["scope_constraint_conflicts_with_execution_commitment:g1:0"]


def test_reference_and_real_population_filter_can_coexist_when_roles_do_not_overlap() -> None:
    from agent_core.lifecycle.goal_planning import _scope_constraint_role_conflict_errors

    goals = [{
        "goal_id": "g1",
        "target_candidate": {"scope_constraints": [{"evidence_span": "over 100"}]},
        "reference_expression": {"evidence_span": "those records"},
    }]
    assert _scope_constraint_role_conflict_errors(
        goals,
        user_text="Of those records, show the ones over 100",
    ) == []


def test_attempt2_repair_is_domain_neutral_and_keeps_capability_registry_out() -> None:
    source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    start = source.index("def _requested_effect_reaudit_collision_guard")
    end = source.index("class ModelGoalAlignmentVerifier", start)
    guard = source[start:end]
    assert "capability_registry" not in guard
    assert "language_interpretation_used" in guard
    blind_start = source.index("blind_dependency_instruction =")
    blind_end = source.index("prompt = {", blind_start)
    policy = source[blind_start:blind_end]
    assert "every supplied scope_constraints entry" in policy
    assert "execution commitment" in policy
    for forbidden in ("快递员", "手机号", "鼠标", "物流", "退款"):
        assert forbidden not in guard
''', encoding="utf-8")


def regenerate_baseline(root: Path, product_sha: str) -> None:
    path = root / BASELINE_PATH
    payload = json.loads(read(path))
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise SystemExit("invalid protected source baseline")
    tracked = subprocess.check_output(
        ["git", "ls-files", "services", "web", "contracts"],
        cwd=root,
        text=True,
    ).splitlines()
    tracked_set = {row.strip() for row in tracked if row.strip()}
    baseline_set = set(files)
    if tracked_set != baseline_set:
        missing = sorted(tracked_set - baseline_set)
        stale = sorted(baseline_set - tracked_set)
        raise SystemExit(
            "protected source set drift: "
            f"missing_from_baseline={missing[:20]} stale_in_baseline={stale[:20]}"
        )
    refreshed: dict[str, str] = {}
    for relative in sorted(baseline_set):
        file_path = root / relative
        if not file_path.is_file():
            raise SystemExit(f"protected source missing: {relative}")
        refreshed[relative] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    payload["files"] = refreshed
    payload["file_count"] = len(refreshed)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["generated_from"] = f"git:{product_sha}"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def patch(root: Path) -> None:
    patch_goal_planning(root)
    patch_protocol(root)
    add_tests(root)
    for relative in (GOAL_PATH, PROTOCOL_PATH, NEW_TEST):
        py_compile.compile(str(root / relative), doraise=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("patch")
    p.add_argument("--workspace", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("--workspace", required=True)
    b.add_argument("--product-sha", required=True)
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.command == "patch":
        patch(root)
    else:
        regenerate_baseline(root, str(args.product_sha))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
