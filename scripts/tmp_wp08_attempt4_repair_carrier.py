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
    if text.count(old) != 1:
        raise SystemExit(f"{label} anchor count={text.count(old)}")
    return text.replace(old, new, 1)


def patch_alignment(root: Path) -> None:
    path = root / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
    text = read(path)
    text = replace_once(
        text,
        '        initial_exact_alignment: GoalAlignmentVerdict | None = None\n        for attempt in range(2):\n            blind_dependency_audit = verifier_repair_kind == "candidate_blind_dependency_reaudit"\n',
        '        initial_exact_alignment: GoalAlignmentVerdict | None = None\n        for attempt in range(3):\n            blind_dependency_audit = str(verifier_repair_kind or "").startswith("candidate_blind_dependency_")\n',
        "alignment bounded retry loop",
    )
    anchor = '            if verdict.verdict in {"exact", "incomplete"}:\n                return verdict\n'
    insertion = '''            if (
                blind_dependency_audit
                and verdict.verdict == "indeterminate"
                and verdict.reason_code.startswith("goal_alignment_dependency_")
                and attempt < 2
            ):
                # The independent semantic authority remains the model, but a
                # malformed pairwise proof is not semantic evidence. Give the
                # same candidate-blind audit one bounded format/grounding retry;
                # never reveal or adopt Planner's candidate dependency graph.
                verifier_repair_kind = "candidate_blind_dependency_format_repair"
                verifier_repair = (
                    "The previous candidate-blind pairwise dependency proof was rejected by the structural grounding contract: "
                    f"{verdict.reason_code}. Re-audit every unordered Goal pair from USER_TEXT only. Assert a dependency only "
                    "when you can copy one literal basis_span from inside the dependent Goal evidence_span and classify it as "
                    "result_reference, result_condition or result_value_input. Shared scope, sentence order, lookup needs and "
                    "business execution prerequisites are not result dependencies. If no grounded positive dependency exists "
                    "for a pair, return relation=independent. Do not fabricate a basis. Return the complete dependency_decisions "
                    "array and the strict JSON fields only."
                )
                prompt = {
                    "USER_TEXT_UNTRUSTED": user_text,
                    "DECLARED_GOALS": _dependency_blind_goal_projection(goals),
                    "RECENT_PUBLIC_CONTEXT": list(recent_public_context or []),
                    "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),
                }
                continue
'''
    text = replace_once(text, anchor, insertion + anchor, "alignment blind format retry")
    write(path, text)


def patch_granularity(root: Path) -> None:
    path = root / "services/agent-service/src/agent_core/lifecycle/goal_granularity.py"
    text = read(path)
    text = replace_once(
        text,
        'GOAL_GRANULARITY_INVENTORY_AUTHORITY_VERSION = "goal-granularity-inventory-authority@2"',
        'GOAL_GRANULARITY_INVENTORY_AUTHORITY_VERSION = "goal-granularity-inventory-authority@3"',
        "granularity authority version",
    )
    text = replace_once(
        text,
        '''def _build_inventory_authority(
    *,
    user_text: str,
    outcome_spans: tuple[str, ...],
    reason_code: str,
    blind_self_audit_attempted: bool,
) -> dict[str, Any]:''',
        '''def _build_inventory_authority(
    *,
    user_text: str,
    outcome_spans: tuple[str, ...],
    reason_code: str,
    blind_self_audit_attempted: bool,
    active_structured_interaction: dict[str, Any] | None = None,
) -> dict[str, Any]:''',
        "granularity authority builder signature",
    )
    text = replace_once(
        text,
        '        "blind_self_audit_attempted": bool(blind_self_audit_attempted),\n',
        '        "blind_self_audit_attempted": bool(blind_self_audit_attempted),\n        "active_structured_interaction_digest": _canonical_digest(dict(active_structured_interaction or {})),\n',
        "granularity authority interaction binding",
    )
    text = replace_once(
        text,
        '''def _validate_inventory_authority(
    *,
    user_text: str,
    authority: Any,
) -> tuple[dict[str, Any] | None, tuple[str, ...], str | None]:''',
        '''def _validate_inventory_authority(
    *,
    user_text: str,
    authority: Any,
    active_structured_interaction: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, tuple[str, ...], str | None]:''',
        "granularity authority validator signature",
    )
    digest_anchor = '''    expected_user_digest = sha256(str(user_text or "").encode("utf-8")).hexdigest()
    if str(authority.get("user_text_sha256") or "") != expected_user_digest:
        return None, (), "goal_granularity_inventory_authority_user_text_mismatch"
'''
    digest_new = digest_anchor + '''    expected_interaction_digest = _canonical_digest(dict(active_structured_interaction or {}))
    if str(authority.get("active_structured_interaction_digest") or "") != expected_interaction_digest:
        return None, (), "goal_granularity_inventory_authority_interaction_mismatch"
'''
    text = replace_once(text, digest_anchor, digest_new, "granularity authority context validation")

    model_signature = '''    def verify(self, *, user_text: str, goals: list[dict[str, Any]]) -> GoalGranularityVerdict:
'''
    text = replace_once(
        text,
        model_signature,
        '''    def verify(
        self,
        *,
        user_text: str,
        goals: list[dict[str, Any]],
        active_structured_interaction: dict[str, Any] | None = None,
    ) -> GoalGranularityVerdict:
''',
        "model granularity signature",
    )
    rule_anchor = '''            "Sentence order or words such as and/then/also/再/然后 do not create an extra outcome by themselves; inventory semantic business results, not conjunction tokens.",
'''
    rule_new = rule_anchor + '''            "A meta-level refusal, deferral or suppression of a prior optional action (for example asking not to proceed, submit or handle it for now) is interaction control, not a separately judgeable business outcome, when there is no matching ACTIVE_STRUCTURED_INTERACTION and the user does not request a business effect on an identified existing object.",
            "A direct business-effect request to cancel/delete/stop an identified existing business object remains an outcome. When ACTIVE_STRUCTURED_INTERACTION identifies a pending user-visible interaction and USER_TEXT explicitly cancels or stops that pending interaction, preserve that control outcome; do not absorb a separate read-only query into it.",
'''
    text = replace_once(text, rule_anchor, rule_new, "granularity interaction-control rules")
    text = replace_once(
        text,
        '                        payload={"USER_TEXT_UNTRUSTED": user_text},\n',
        '                        payload={\n                            "USER_TEXT_UNTRUSTED": user_text,\n                            "ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {}),\n                        },\n',
        "granularity verifier payload context",
    )
    text = replace_once(
        text,
        '''                blind_self_audit_attempted=attempt > 0,
            )''',
        '''                blind_self_audit_attempted=attempt > 0,
                active_structured_interaction=active_structured_interaction,
            )''',
        "granularity authority build call",
    )

    verify_anchor = '''def verify_goal_granularity(
    *,
    state: dict[str, Any],
    goals: list[dict[str, Any]],
) -> GoalGranularityVerdict:
    user_text = _text(state.get("current_user_input"), limit=20_000)
'''
    verify_new = '''def _active_structured_interaction_context(state: dict[str, Any]) -> dict[str, Any] | None:
    """Project only public pending-interaction identity for outcome inventory."""
    from agent_core.transaction.interaction import interaction_response_contract

    contract = interaction_response_contract(state)
    interaction = (
        contract.get("interaction")
        if isinstance(contract, dict) and isinstance(contract.get("interaction"), dict)
        else None
    )
    if interaction is None:
        return None
    return {
        "interaction_id": str(interaction.get("interaction_id") or ""),
        "lifecycle": str(interaction.get("lifecycle") or ""),
        "title": str(interaction.get("title") or ""),
        "target": str(interaction.get("target") or ""),
        "required_fields": [
            str(row.get("name") or "")
            for row in list(interaction.get("fields") or [])
            if isinstance(row, dict) and str(row.get("name") or "")
        ],
        "chat_write_authorized": False,
        "runtime_redirect_required": True,
    }


def verify_goal_granularity(
    *,
    state: dict[str, Any],
    goals: list[dict[str, Any]],
) -> GoalGranularityVerdict:
    user_text = _text(state.get("current_user_input"), limit=20_000)
    active_structured_interaction = _active_structured_interaction_context(state)
'''
    text = replace_once(text, verify_anchor, verify_new, "granularity trusted context helper")
    text = replace_once(
        text,
        '''        validated_authority, outcome_spans, authority_error = _validate_inventory_authority(
            user_text=user_text,
            authority=frozen_authority,
        )''',
        '''        validated_authority, outcome_spans, authority_error = _validate_inventory_authority(
            user_text=user_text,
            authority=frozen_authority,
            active_structured_interaction=active_structured_interaction,
        )''',
        "granularity frozen context validation",
    )
    text = replace_once(
        text,
        '        raw = verifier.verify(user_text=user_text, goals=goals)\n',
        '''        raw = (
            verifier.verify(
                user_text=user_text,
                goals=goals,
                active_structured_interaction=active_structured_interaction,
            )
            if isinstance(verifier, ModelGoalGranularityVerifier)
            else verifier.verify(user_text=user_text, goals=goals)
        )
''',
        "granularity model context invocation",
    )
    write(path, text)


def patch_existing_regression(root: Path) -> None:
    path = root / "services/agent-service/tests/runtime/test_wp08_attempt5_dependency_authority.py"
    text = read(path)
    old = '''    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[bad, blind_bad]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.verdict == "indeterminate"
    assert verdict.reason_code == "goal_alignment_dependency_basis_not_in_dependent_goal:0"
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_reaudit"
'''
    new = '''    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[bad, blind_bad, blind_bad]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 3
    assert verdict.verdict == "indeterminate"
    assert verdict.reason_code == "goal_alignment_dependency_basis_not_in_dependent_goal:0"
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_format_repair"
'''
    text = replace_once(text, old, new, "existing malformed dependency regression")
    write(path, text)


def add_followup_tests(root: Path) -> None:
    path = root / "skill-system/tests/test_wp08_attempt4_followup_repair.py"
    if path.exists():
        raise SystemExit("follow-up test file already exists")
    path.write_text(r'''from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "services" / "agent-service"
SRC = AGENT / "src"
for value in (AGENT, SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))


def _response(payload: dict):
    return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)), {}


def _goal(goal_id: str, span: str, depends_on: list[str]) -> dict:
    return {"goal_id": goal_id, "evidence_span": span, "depends_on": depends_on}


def test_attempt4_refund_scope_recovers_only_after_grounded_blind_format_retry() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下鼠标订单，然后帮我申请退款"
    goals = [_goal("g1", "查一下鼠标订单", []), _goal("g2", "帮我申请退款", [])]
    first_false_positive = _response({
        "verdict": "incomplete",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_edges": [{
            "dependent_goal_id": "g2",
            "requires_result_of_goal_id": "g1",
            "basis_kind": "result_reference",
            "basis_span": "帮我申请退款",
        }],
        "reason_code": "execution_prerequisite_confused_with_result_dependency",
    })
    malformed_blind = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "查一下鼠标订单",
        }],
        "reason_code": "bad_blind_basis",
    })
    grounded_independent = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下鼠标订单", "帮我申请退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "independent",
        }],
        "reason_code": "shared_scope_is_not_result_dependency",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        side_effect=[first_false_positive, malformed_blind, grounded_independent],
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 3
    assert verdict.exact
    assert verdict.details["dependency_graph_match"] is True
    assert verdict.details["dependency_edges"] == []
    assert verdict.details["verifier_repair_kind"] == "candidate_blind_dependency_format_repair"
    third = str(invoke.call_args_list[2].kwargs["payload"])
    assert '"depends_on"' not in third
    assert "structural grounding contract" in third


def test_true_literal_result_reference_does_not_need_third_retry() -> None:
    from agent_core.lifecycle.goal_planning import ModelGoalAlignmentVerifier

    text = "查一下键盘订单，再看看它能不能退款"
    goals = [_goal("g1", "查一下键盘订单", []), _goal("g2", "再看看它能不能退款", ["g1"])]
    edge = {
        "dependent_goal_id": "g2",
        "requires_result_of_goal_id": "g1",
        "basis_kind": "result_reference",
        "basis_span": "它",
    }
    first = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
        "missing_spans": [],
        "dependency_edges": [edge],
        "reason_code": "literal_result_reference",
    })
    blind = _response({
        "verdict": "exact",
        "evidence_spans": ["查一下键盘订单", "再看看它能不能退款"],
        "missing_spans": [],
        "dependency_decisions": [{
            "goal_a_id": "g1",
            "goal_b_id": "g2",
            "relation": "b_depends_on_a",
            "basis_kind": "result_reference",
            "basis_span": "它",
        }],
        "reason_code": "literal_result_reference_confirmed",
    })
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model", side_effect=[first, blind]
    ) as invoke:
        verdict = ModelGoalAlignmentVerifier().verify(user_text=text, goals=goals, known_tools=set())
    assert invoke.call_count == 2
    assert verdict.exact
    assert verdict.details["dependency_edges"][0]["basis_span"] == "它"


def test_goal_inventory_receives_pending_interaction_context_and_excludes_meta_deferral() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    text = "先不办理。那无线鼠标什么时候发货？"
    goals = [_goal("g1", "无线鼠标什么时候发货", [])]
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        return_value=_response({
            "verdict": "exact",
            "outcome_spans": ["无线鼠标什么时候发货"],
            "reason_code": "deferral_is_interaction_control_query_is_outcome",
        }),
    ) as invoke:
        verdict = ModelGoalGranularityVerifier().verify(
            user_text=text,
            goals=goals,
            active_structured_interaction=None,
        )
    assert verdict.exact
    payload = str(invoke.call_args.kwargs["payload"])
    assert "ACTIVE_STRUCTURED_INTERACTION" in payload
    assert "meta-level refusal" in payload
    assert "direct business-effect request" in payload


def test_pending_interaction_can_still_be_an_explicit_control_outcome() -> None:
    from agent_core.lifecycle.goal_granularity import ModelGoalGranularityVerifier

    text = "把这个申请停掉，再查无线鼠标物流"
    goals = [_goal("g1", "把这个申请停掉", []), _goal("g2", "查无线鼠标物流", [])]
    active = {
        "interaction_id": "interaction:refund:1",
        "lifecycle": "pending",
        "title": "退款申请",
        "target": "订单10001",
        "required_fields": [],
        "chat_write_authorized": False,
        "runtime_redirect_required": True,
    }
    with patch("agent_core.config.get_model", return_value=object()), patch(
        "agent_core.model_calls.invoke_model",
        return_value=_response({
            "verdict": "exact",
            "outcome_spans": ["把这个申请停掉", "查无线鼠标物流"],
            "reason_code": "pending_control_plus_read_query",
        }),
    ):
        verdict = ModelGoalGranularityVerifier().verify(
            user_text=text,
            goals=goals,
            active_structured_interaction=active,
        )
    assert verdict.exact
    assert verdict.details["inventory_outcome_count"] == 2


def test_frozen_inventory_authority_is_bound_to_interaction_snapshot() -> None:
    from agent_core.lifecycle.goal_granularity import (
        _build_inventory_authority,
        _validate_inventory_authority,
    )

    text = "先不办理。那无线鼠标什么时候发货？"
    authority = _build_inventory_authority(
        user_text=text,
        outcome_spans=("无线鼠标什么时候发货",),
        reason_code="query_only",
        blind_self_audit_attempted=False,
        active_structured_interaction=None,
    )
    valid, spans, error = _validate_inventory_authority(
        user_text=text,
        authority=authority,
        active_structured_interaction=None,
    )
    assert error is None
    assert valid is not None
    assert spans == ("无线鼠标什么时候发货",)
    changed = {"interaction_id": "interaction:new", "lifecycle": "pending"}
    invalid, _, changed_error = _validate_inventory_authority(
        user_text=text,
        authority=authority,
        active_structured_interaction=changed,
    )
    assert invalid is None
    assert changed_error == "goal_granularity_inventory_authority_interaction_mismatch"
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
        patch_alignment(root)
        patch_granularity(root)
        patch_existing_regression(root)
        add_followup_tests(root)
    else:
        if not args.product_sha:
            raise SystemExit("--product-sha required")
        regenerate_baseline(root, args.product_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
