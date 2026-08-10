#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor count={count}")
    return text.replace(old, new, 1)


def patch_goal_alignment(root: Path) -> None:
    path = root / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
    text = read(path)

    old_incomplete = '''                    verifier_repair = (
                        "Re-audit the previous incomplete claim from scratch against the same USER_TEXT and DECLARED_GOALS. "
                        "The prior claim did not identify any machine-grounded omitted outcome, so it is not authoritative. "
                        "If the declaration is truly incomplete, copy every omitted user-observable outcome into missing_spans "
                        "as an exact literal contiguous substring of USER_TEXT. Do not paraphrase, infer a hidden prerequisite, "
                        "invent a target-resolution step, or use tool/capability/oracle knowledge. If no literal omitted outcome "
                        "can be identified after re-audit, withdraw the incomplete claim and return exact with literal "
                        "evidence_spans. Return only verdict, evidence_spans, missing_spans and reason_code."
                    )
'''
    new_incomplete = '''                    verifier_repair = (
                        "Re-audit the previous incomplete claim from scratch against the same USER_TEXT and DECLARED_GOALS. "
                        "The prior claim did not identify any machine-grounded omitted outcome, so it is not authoritative. "
                        "If the declaration is truly incomplete, copy every omitted user-observable outcome into missing_spans "
                        "as an exact literal contiguous substring of USER_TEXT. Do not paraphrase, infer a hidden prerequisite, "
                        "invent a target-resolution step, or use tool/capability/oracle knowledge. If no literal omitted outcome "
                        "can be identified after re-audit, withdraw the incomplete claim and return exact with literal "
                        "evidence_spans. Preserve the normal machine contract: return only verdict, evidence_spans, missing_spans, "
                        "dependency_edges and reason_code. dependency_edges must still be the complete independently judged current-turn "
                        "result-dependency graph; for a single Goal it must be an empty list."
                    )
'''
    text = replace_once(text, old_incomplete, new_incomplete, "incomplete grounding repair contract")

    old_exact = '''                    verifier_repair = (
                        "Re-audit the previous exact claim against the same USER_TEXT and DECLARED_GOALS. The prior exact "
                        "claim lacked machine-grounded evidence. If exact, copy literal contiguous USER_TEXT spans that cover "
                        "the preserved requested outcomes into evidence_spans. If it is not exact, return incomplete or clarify "
                        "only with the normal strict contract; any missing_spans must be literal USER_TEXT substrings. Do not "
                        "use tool/capability/oracle knowledge. Return only verdict, evidence_spans, missing_spans and reason_code."
                    )
'''
    new_exact = '''                    verifier_repair = (
                        "Re-audit the previous exact claim against the same USER_TEXT and DECLARED_GOALS. The prior exact "
                        "claim lacked machine-grounded evidence. If exact, copy literal contiguous USER_TEXT spans that cover "
                        "the preserved requested outcomes into evidence_spans. If it is not exact, return incomplete or clarify "
                        "only with the normal strict contract; any missing_spans must be literal USER_TEXT substrings. Do not "
                        "use tool/capability/oracle knowledge. Preserve the normal machine contract: return only verdict, "
                        "evidence_spans, missing_spans, dependency_edges and reason_code. dependency_edges must still be the complete "
                        "independently judged current-turn result-dependency graph; for a single Goal it must be an empty list."
                    )
'''
    text = replace_once(text, old_exact, new_exact, "exact grounding repair contract")
    write(path, text)


def add_tests(root: Path) -> None:
    path = root / "skill-system/tests/test_wp08_attempt5_followup_repair.py"
    if path.exists():
        raise SystemExit("Attempt 5 follow-up test file already exists")
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


def _goal(text: str) -> dict:
    return {
        "goal_id": "g1",
        "description": "判断退货退款资格",
        "evidence_span": text,
        "requested_effect": {
            "domain": "after_sales",
            "operation": "check_return_refund_eligibility",
            "object_type": "order",
            "raw_description": text,
        },
        "expected_result_cardinality": "single",
        "required": True,
        "depends_on": [],
    }


def test_attempt5_ambiguous_target_reaudit_can_freeze_outcome_without_inventing_target() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "可以退货退款吗？"
    first = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["具体订单"],
        "dependency_edges": [],
        "reason_code": "target_not_selected",
    })
    second = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "requested_outcome_preserved_target_selection_downstream",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, second]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[_goal(text)],
            known_tools=set(),
            recent_public_context=[{
                "turn": 1,
                "user_summary": "我买过什么？",
                "answer_summary": "展示了四笔订单",
                "result_handles": ["h_result:orders"],
                "historical_only": True,
            }],
        )

    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["verifier_repair_kind"] == "incomplete_claim_grounding_reaudit"
    repair_message = invoke.call_args_list[1].kwargs["payload"][-1].content
    assert "dependency_edges" in repair_message
    assert "single Goal" in repair_message
    assert "target-resolution step" in repair_message


def test_grounding_reaudit_still_fails_closed_when_dependency_graph_field_is_omitted() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "可以退货退款吗？"
    first = _response({
        "verdict": "incomplete",
        "evidence_spans": [text],
        "missing_spans": ["具体订单"],
        "dependency_edges": [],
        "reason_code": "target_not_selected",
    })
    malformed_exact = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "reason_code": "exact_but_dependency_graph_omitted",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first, malformed_exact, malformed_exact],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[_goal(text)],
            known_tools=set(),
        )

    assert invoke.call_count == 3
    assert verdict.verdict == "indeterminate"
    assert verdict.reason_code == "goal_alignment_dependency_edges_required"


def test_exact_claim_grounding_reaudit_also_preserves_complete_dependency_contract() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "可以退货退款吗？"
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["退货退款资格"],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "ungrounded_exact",
    })
    second = _response({
        "verdict": "exact",
        "evidence_spans": [text],
        "missing_spans": [],
        "dependency_edges": [],
        "reason_code": "grounded_exact",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, second]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(
            user_text=text,
            goals=[_goal(text)],
            known_tools=set(),
        )

    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    repair_message = invoke.call_args_list[1].kwargs["payload"][-1].content
    assert "dependency_edges" in repair_message
    assert "single Goal" in repair_message


def test_target_member_selection_remains_post_freeze_runtime_concern() -> None:
    goal_source = (AGENT_SRC / "agent_core/lifecycle/goal_planning.py").read_text(encoding="utf-8")
    dialogue_source = (AGENT_SRC / "agent_core/lifecycle/dialogue_runtime.py").read_text(encoding="utf-8")
    protocol_source = (AGENT_SRC / "agent_core/lifecycle/protocol.py").read_text(encoding="utf-8")

    assert "target-member selection" in goal_source
    assert "downstream Runtime concerns" in goal_source
    assert "_clarification_terminal_goal_ids" in dialogue_source
    assert '"missing_kind": {"type": "string", "enum": ["target", "scope", "condition", "intent"]}' in protocol_source
    assert "target_not_selected" not in goal_source
    assert "可以退货退款吗" not in goal_source
''', encoding="utf-8")


def regenerate_baseline(root: Path, product_sha: str) -> None:
    path = root / "skill-system/registry/product-source-baseline.json"
    payload = json.loads(read(path))
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise SystemExit("invalid protected source baseline")
    missing: list[str] = []
    for relative in sorted(files):
        file_path = root / relative
        if not file_path.is_file():
            missing.append(relative)
            continue
        files[relative] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    if missing:
        raise SystemExit("missing protected baseline paths: " + ", ".join(missing[:10]))
    payload["file_count"] = len(files)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["generated_from"] = "git:" + product_sha
    write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("patch", "baseline"))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--product-sha", default="")
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if args.mode == "patch":
        patch_goal_alignment(root)
        add_tests(root)
    else:
        if not args.product_sha:
            raise SystemExit("--product-sha required")
        regenerate_baseline(root, args.product_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
