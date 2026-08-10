from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
GOAL = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
DIALOGUE = ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
SEMANTIC_SMOKE = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
TEST = ROOT / "skill-system/tests/test_wp08_attempt1_product_repair.py"
BASELINE = ROOT / "skill-system/registry/product-source-baseline.json"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


goal = GOAL.read_text(encoding="utf-8")
helper = '''\n\ndef _model_alignment_pairwise_dependency_proof(\n    *,\n    user_text: str,\n    goals: list[dict[str, Any]],\n    values: Any,\n) -> tuple[dict[str, Any], str | None]:\n    """Validate a candidate-blind, pairwise-complete dependency audit.\n\n    An empty edge list is not evidence that a multi-goal graph is complete.\n    The blind second verifier call must explicitly judge every unordered Goal\n    pair as dependent in one direction or independent. Runtime validates only\n    pair coverage, Goal IDs and literal grounding for positive dependency\n    edges; it never infers a dependency from user vocabulary.\n    """\n    goal_by_id = {\n        str(goal.get("goal_id") or ""): goal\n        for goal in goals\n        if str(goal.get("goal_id") or "")\n    }\n    goal_ids = list(goal_by_id)\n    declared_edges = {\n        (str(goal.get("goal_id") or ""), str(prerequisite))\n        for goal in goals\n        for prerequisite in list(goal.get("depends_on") or [])\n        if str(goal.get("goal_id") or "") and str(prerequisite)\n    }\n    expected_pairs = {\n        tuple(sorted((goal_ids[left], goal_ids[right])))\n        for left in range(len(goal_ids))\n        for right in range(left + 1, len(goal_ids))\n    }\n    base_details: dict[str, Any] = {\n        "dependency_authority": "independent_goal_alignment",\n        "dependency_proof_complete": False,\n        "dependency_graph_match": False,\n        "declared_dependency_edges": [\n            {\n                "dependent_goal_id": dependent,\n                "requires_result_of_goal_id": prerequisite,\n            }\n            for dependent, prerequisite in sorted(declared_edges)\n        ],\n        "dependency_edges": [],\n        "dependency_pair_decisions": [],\n        "expected_pair_count": len(expected_pairs),\n    }\n    if not isinstance(values, list):\n        return base_details, "goal_alignment_dependency_decisions_required"\n\n    seen_pairs: set[tuple[str, str]] = set()\n    proof_edges: set[tuple[str, str]] = set()\n    proof_rows: list[dict[str, Any]] = []\n    decision_rows: list[dict[str, Any]] = []\n    allowed_relations = {"a_depends_on_b", "b_depends_on_a", "independent"}\n    for index, raw in enumerate(values):\n        if not isinstance(raw, dict):\n            return base_details, f"goal_alignment_dependency_decision_invalid:{index}"\n        goal_a = _clean_text(raw.get("goal_a_id"), limit=80)\n        goal_b = _clean_text(raw.get("goal_b_id"), limit=80)\n        relation = _clean_text(raw.get("relation"), limit=80).lower()\n        if goal_a not in goal_by_id or goal_b not in goal_by_id or goal_a == goal_b:\n            return base_details, f"goal_alignment_dependency_decision_goal_invalid:{index}"\n        pair = tuple(sorted((goal_a, goal_b)))\n        if pair not in expected_pairs:\n            return base_details, f"goal_alignment_dependency_decision_pair_unknown:{index}"\n        if pair in seen_pairs:\n            return base_details, f"goal_alignment_dependency_decision_duplicate_pair:{index}"\n        if relation not in allowed_relations:\n            return base_details, f"goal_alignment_dependency_decision_relation_invalid:{index}"\n        seen_pairs.add(pair)\n        decision_row: dict[str, Any] = {\n            "goal_a_id": goal_a,\n            "goal_b_id": goal_b,\n            "relation": relation,\n        }\n        if relation != "independent":\n            dependent = goal_a if relation == "a_depends_on_b" else goal_b\n            prerequisite = goal_b if relation == "a_depends_on_b" else goal_a\n            basis_kind = _clean_text(raw.get("basis_kind"), limit=80).lower()\n            basis_span = _clean_text(raw.get("basis_span"), limit=240)\n            dependent_span = _clean_text(goal_by_id[dependent].get("evidence_span"), limit=240)\n            if basis_kind not in _ALLOWED_ALIGNMENT_DEPENDENCY_BASIS_KINDS:\n                return base_details, f"goal_alignment_dependency_basis_kind_invalid:{index}"\n            if (\n                not basis_span\n                or basis_span not in user_text\n                or not dependent_span\n                or basis_span not in dependent_span\n            ):\n                return base_details, f"goal_alignment_dependency_basis_not_in_dependent_goal:{index}"\n            edge = (dependent, prerequisite)\n            proof_edges.add(edge)\n            proof_rows.append({\n                "dependent_goal_id": dependent,\n                "requires_result_of_goal_id": prerequisite,\n                "basis_kind": basis_kind,\n                "basis_span": basis_span,\n            })\n            decision_row.update({"basis_kind": basis_kind, "basis_span": basis_span})\n        decision_rows.append(decision_row)\n\n    missing_pairs = sorted(expected_pairs - seen_pairs)\n    extra_pairs = sorted(seen_pairs - expected_pairs)\n    details = {\n        **base_details,\n        "dependency_proof_complete": not missing_pairs and not extra_pairs,\n        "dependency_graph_match": (\n            not missing_pairs and not extra_pairs and proof_edges == declared_edges\n        ),\n        "dependency_edges": sorted(\n            proof_rows,\n            key=lambda row: (\n                str(row["dependent_goal_id"]),\n                str(row["requires_result_of_goal_id"]),\n            ),\n        ),\n        "dependency_pair_decisions": sorted(\n            decision_rows,\n            key=lambda row: tuple(sorted((str(row["goal_a_id"]), str(row["goal_b_id"])))),\n        ),\n        "missing_dependency_pairs": [list(pair) for pair in missing_pairs],\n    }\n    if missing_pairs or extra_pairs:\n        return details, "goal_alignment_dependency_pair_coverage_incomplete"\n    if proof_edges != declared_edges:\n        return details, "goal_alignment_dependency_graph_mismatch"\n    return details, None\n'''
goal = replace_once(
    goal,
    "\n\ndef _dependency_blind_goal_projection(goals: list[dict[str, Any]]) -> list[dict[str, Any]]:\n",
    helper + "\n\ndef _dependency_blind_goal_projection(goals: list[dict[str, Any]]) -> list[dict[str, Any]]:\n",
    label="insert pairwise dependency proof",
)
goal = replace_once(
    goal,
    '''            effective_instruction = blind_dependency_instruction if blind_dependency_audit else instruction\n            effective_rules = blind_dependency_rules if blind_dependency_audit else decision_rules\n''',
    '''            effective_instruction = (\n                blind_dependency_instruction\n                + " For this candidate-blind audit, dependency absence must also be proven. "\n                "Return dependency_decisions with exactly one row for every unordered pair of supplied Goal IDs. "\n                "Each row has goal_a_id, goal_b_id and relation=a_depends_on_b|b_depends_on_a|independent. "\n                "For a dependency relation also include basis_kind=result_reference|result_condition|result_value_input "\n                "and basis_span copied literally from inside the dependent Goal evidence_span. "\n                "Do not omit a pair merely because you believe it is independent; an empty dependency_decisions list "\n                "is valid only when fewer than two Goals are supplied. Return JSON only with verdict, evidence_spans, "\n                "missing_spans, dependency_decisions and reason_code."\n                if blind_dependency_audit else instruction\n            )\n            effective_rules = blind_dependency_rules if blind_dependency_audit else decision_rules\n''',
    label="blind verifier pairwise contract",
)
goal = replace_once(
    goal,
    '''                if raw_verdict in {"exact", "incomplete"}:\n                    dependency_details, dependency_error = _model_alignment_dependency_proof(\n                        user_text=user_text,\n                        goals=goals,\n                        values=parsed.get("dependency_edges"),\n                    )\n''',
    '''                if raw_verdict in {"exact", "incomplete"}:\n                    if blind_dependency_audit:\n                        dependency_details, dependency_error = _model_alignment_pairwise_dependency_proof(\n                            user_text=user_text,\n                            goals=goals,\n                            values=parsed.get("dependency_decisions"),\n                        )\n                    else:\n                        dependency_details, dependency_error = _model_alignment_dependency_proof(\n                            user_text=user_text,\n                            goals=goals,\n                            values=parsed.get("dependency_edges"),\n                        )\n''',
    label="use pairwise proof for blind audit",
)
GOAL.write_text(goal, encoding="utf-8")

dialogue = DIALOGUE.read_text(encoding="utf-8")
helper2 = '''\n\ndef _goal_declaration_protocol_repair_rule(state: dict[str, Any]) -> str | None:\n    """Return a tool-only planning repair after a model emitted prose.\n\n    This is protocol feedback only. It does not infer a Goal, target, intent or\n    business fact from the prose; the model must redeclare the same user turn\n    through the normal semantic authority on its one remaining bounded retry.\n    """\n    if str(state.get("status") or "") != "GoalDeclarationProtocolRetry":\n        return None\n    return (\n        "上一次统一语义声明没有产生且仅产生一次 declare_turn_goals 调用；纯文本回答不能建立本轮正式语义，也不会发送给用户。"\n        "本次仍处于同一个用户回合的语义声明阶段，必须只调用一次 declare_turn_goals，重新完整声明当前用户原话中的 Goal、条件和依赖；"\n        "不得直接回答用户、不得调用业务能力、不得根据上一次纯文本自行冻结语义。"\n    )\n'''
dialogue = replace_once(
    dialogue,
    'FINAL_PROTOCOL_MAX_RETRIES = 1\nGOAL_DECLARATION_MAX_RETRIES = 2\n',
    'FINAL_PROTOCOL_MAX_RETRIES = 1\nGOAL_DECLARATION_MAX_RETRIES = 2\n' + helper2,
    label="insert goal declaration protocol repair helper",
)
dialogue = replace_once(
    dialogue,
    '''    if declaration_clarification_mode:\n        protocol_repair_rule = (\n''',
    '''    goal_declaration_protocol_repair = _goal_declaration_protocol_repair_rule(state)\n    if declaration_clarification_mode:\n        protocol_repair_rule = (\n''',
    label="compute goal declaration protocol repair",
)
dialogue = replace_once(
    dialogue,
    '''    elif str(state.get("status") or "") == "ClarificationNotNeededRetry":\n''',
    '''    elif goal_declaration_protocol_repair is not None:\n        protocol_repair_rule = goal_declaration_protocol_repair\n    elif str(state.get("status") or "") == "ClarificationNotNeededRetry":\n''',
    label="apply goal declaration protocol repair",
)
DIALOGUE.write_text(dialogue, encoding="utf-8")

semantic = SEMANTIC_SMOKE.read_text(encoding="utf-8")
semantic = replace_once(
    semantic,
    '''            raise RuntimeError(\n                f"{case_id}: goal dependency mismatch for oracle {expected['oracle_id']}"\n            )\n''',
    '''            raise RuntimeError(\n                f"{case_id}: goal dependency mismatch for oracle {expected['oracle_id']}; "\n                f"expected_dependencies={sorted(expected_dependencies)!r}; "\n                f"actual_dependencies={sorted(actual_dependencies)!r}"\n            )\n''',
    label="improve semantic dependency diagnostic",
)
SEMANTIC_SMOKE.write_text(semantic, encoding="utf-8")

TEST.write_text(r'''from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
AGENT_SRC = ROOT / "services/agent-service/src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from agent_core.lifecycle.goal_planning import _model_alignment_pairwise_dependency_proof
from agent_core.lifecycle.dialogue_runtime import (
    GOAL_DECLARATION_MAX_RETRIES,
    _goal_declaration_protocol_repair_rule,
)


def _goals(*, with_dependency: bool) -> list[dict]:
    return [
        {
            "goal_id": "g1",
            "evidence_span": "find the order",
            "depends_on": [],
        },
        {
            "goal_id": "g2",
            "evidence_span": "assess that result",
            "depends_on": ["g1"] if with_dependency else [],
        },
    ]


class PairwiseDependencyProofTests(unittest.TestCase):
    def test_pairwise_audit_cannot_prove_multi_goal_graph_with_empty_decisions(self) -> None:
        details, error = _model_alignment_pairwise_dependency_proof(
            user_text="find the order, then assess that result",
            goals=_goals(with_dependency=False),
            values=[],
        )
        self.assertEqual(error, "goal_alignment_dependency_pair_coverage_incomplete")
        self.assertFalse(details["dependency_proof_complete"])
        self.assertEqual(details["missing_dependency_pairs"], [["g1", "g2"]])

    def test_pairwise_audit_exposes_grounded_edge_when_candidate_omits_it(self) -> None:
        details, error = _model_alignment_pairwise_dependency_proof(
            user_text="find the order, then assess that result",
            goals=_goals(with_dependency=False),
            values=[{
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "b_depends_on_a",
                "basis_kind": "result_reference",
                "basis_span": "that result",
            }],
        )
        self.assertEqual(error, "goal_alignment_dependency_graph_mismatch")
        self.assertTrue(details["dependency_proof_complete"])
        self.assertFalse(details["dependency_graph_match"])
        self.assertEqual(details["dependency_edges"], [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "that result",
        }])

    def test_pairwise_audit_accepts_same_grounded_edge_when_candidate_declares_it(self) -> None:
        details, error = _model_alignment_pairwise_dependency_proof(
            user_text="find the order, then assess that result",
            goals=_goals(with_dependency=True),
            values=[{
                "goal_a_id": "g1",
                "goal_b_id": "g2",
                "relation": "b_depends_on_a",
                "basis_kind": "result_reference",
                "basis_span": "that result",
            }],
        )
        self.assertIsNone(error)
        self.assertTrue(details["dependency_proof_complete"])
        self.assertTrue(details["dependency_graph_match"])


class GoalDeclarationProtocolRepairTests(unittest.TestCase):
    def test_no_tool_planning_retry_has_explicit_tool_only_feedback(self) -> None:
        rule = _goal_declaration_protocol_repair_rule({"status": "GoalDeclarationProtocolRetry"})
        self.assertIsNotNone(rule)
        self.assertIn("declare_turn_goals", rule or "")
        self.assertIn("纯文本", rule or "")
        self.assertIn("必须只调用一次", rule or "")
        self.assertEqual(GOAL_DECLARATION_MAX_RETRIES, 2)

    def test_unrelated_status_does_not_invent_repair_semantics(self) -> None:
        self.assertIsNone(_goal_declaration_protocol_repair_rule({"status": "GroundedFinalAnswer"}))

    def test_repair_is_protocol_generic_not_invoice_or_recall_keyword_logic(self) -> None:
        source = (AGENT_SRC / "agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
        helper = source.split("def _goal_declaration_protocol_repair_rule", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("开票", helper)
        self.assertNotIn("刚才", helper)
        self.assertNotIn("10004", helper)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

payload = json.loads(BASELINE.read_text(encoding="utf-8"))
roots = [str(value) for value in payload.get("protected_roots") or ()]
raw = subprocess.check_output(["git", "ls-files", "-z", "--", *roots], cwd=ROOT)
tracked = sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
payload["generated_from"] = "git:" + subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
payload["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
payload["file_count"] = len(tracked)
payload["files"] = {
    relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    for relative in tracked
}
BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

print(json.dumps({
    "status": "PATCHED",
    "files": [
        str(GOAL.relative_to(ROOT)),
        str(DIALOGUE.relative_to(ROOT)),
        str(SEMANTIC_SMOKE.relative_to(ROOT)),
        str(TEST.relative_to(ROOT)),
        str(BASELINE.relative_to(ROOT)),
    ],
}, ensure_ascii=False))
