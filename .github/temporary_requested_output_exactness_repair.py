from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
GOAL = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
DIALOGUE = ROOT / "services/agent-service/src/agent_core/lifecycle/dialogue_runtime.py"
TEST = ROOT / "services/agent-service/tests/runtime/test_requested_output_exactness_redeclaration_feedback.py"
TRIGGER = ROOT / ".github/release-trigger"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


goal = GOAL.read_text(encoding="utf-8")

helper = '''\n\ndef _semantic_vocabulary_for_alignment() -> dict[str, Any]:
    """Expose only capability-independent canonical meanings to the verifier.

    The semantic verifier must know what a registered ``output_id`` actually
    means before it can reject a nearby-but-different identity.  This projection
    deliberately strips migration aliases, tool names, capability availability
    and every execution signal; it cannot tell the verifier whether an output is
    implemented, only the domain meaning contributed by the installed module.
    """
    try:
        from agent_core.modules.registry import current_module_registry
        snapshot = current_module_registry().semantic_vocabulary_snapshot()
    except RuntimeError:
        snapshot = {}
    outputs: list[dict[str, Any]] = []
    for raw in list(snapshot.get("outputs") or []):
        if not isinstance(raw, dict):
            continue
        output_id = _clean_text(raw.get("output_id"), limit=240).casefold()
        subject_type = _clean_text(raw.get("subject_type"), limit=120).casefold()
        description = _clean_text(raw.get("description"), limit=1000)
        effect_kinds = [
            _clean_text(value, limit=80).casefold()
            for value in list(raw.get("effect_kinds") or [])
            if _clean_text(value, limit=80)
        ]
        if not output_id or not subject_type or not description or not effect_kinds:
            continue
        outputs.append({
            "output_id": output_id,
            "subject_type": subject_type,
            "effect_kinds": list(dict.fromkeys(effect_kinds)),
            "description": description,
        })
    return {
        "version": "semantic-output-vocabulary@1",
        "authority": "domain_semantics_only_capability_independent",
        "availability_exposed": False,
        "tool_names_exposed": False,
        "outputs": sorted(outputs, key=lambda row: str(row["output_id"])),
    }
'''
goal = replace_once(
    goal,
    "\n\nclass ModelGoalAlignmentVerifier:",
    helper + "\n\nclass ModelGoalAlignmentVerifier:",
    "insert semantic alignment vocabulary projection",
)

goal = replace_once(
    goal,
    "            structured_verifier_messages,\n        )\n\n        instruction = (",
    "            structured_verifier_messages,\n        )\n\n        semantic_vocabulary = _semantic_vocabulary_for_alignment()\n\n        instruction = (",
    "bind semantic vocabulary to model verifier",
)

goal = replace_once(
    goal,
    '            "requested_effect must preserve the user\'s business effect even when the current system may not implement it; never rewrite an unsupported effect to a nearby available effect",',
    '            "requested_effect must preserve the user\'s business effect even when the current system may not implement it; never rewrite an unsupported effect to a nearby available effect. When requested_outputs selects a registered output_id, judge that identity against CANONICAL_SEMANTIC_OUTPUT_VOCABULARY.description, not the identifier name alone. If USER_TEXT requests a materially different user-visible information dimension or outcome that no registered description represents exactly, using a nearby registered output_id is semantic substitution and verdict must be incomplete; the reserved open identity is then required. Capability availability remains forbidden evidence",',
    "strengthen visible requested-output exactness rule",
)

goal = replace_once(
    goal,
    '            "(2) whether each DECLARED_GOAL.requested_effect preserves the customer\'s actual business effect instead of "\n            "coercing an unsupported/open effect into a nearby registered effect; (3) whether every explicit user-stated "',
    '            "(2) whether each DECLARED_GOAL.requested_effect preserves the customer\'s actual business effect instead of "\n            "coercing an unsupported/open effect into a nearby registered effect; use CANONICAL_SEMANTIC_OUTPUT_VOCABULARY descriptions as the meaning authority for registered requested_outputs, and require open when the requested information dimension/outcome has no exact registered meaning; (3) whether every explicit user-stated "',
    "strengthen blind requested-output exactness instruction",
)

goal = replace_once(
    goal,
    '            "requested_effect fidelity is judged against the literal business effect in each Goal evidence_span; nearby registered capability identity is never acceptable merely because it exists",',
    '            "requested_effect fidelity is judged against the literal business effect in each Goal evidence_span and the capability-independent canonical vocabulary description; a nearby registered semantic identity is never acceptable merely because its name is related, and when no registered description exactly represents the requested outcome the declaration must retain open",',
    "strengthen blind requested-output rule",
)

goal = replace_once(
    goal,
    '                    "business effect. An unsupported/unregistered effect or harmless naming granularity is not "\n                    "itself a mismatch, and capability availability must not be used as evidence. Withdraw the mismatch only when the "',
    '                    "business effect. An unsupported/unregistered effect or harmless naming granularity is not "\n                    "itself a mismatch, but a registered requested_outputs identity whose CANONICAL_SEMANTIC_OUTPUT_VOCABULARY description does not cover the literal requested information dimension/outcome is a real mismatch; when no registered description matches exactly, open is the only faithful identity. Capability availability must not be used as evidence. Withdraw the mismatch only when the "',
    "strengthen requested-effect reaudit",
)

pattern = re.compile(r'(?P<indent>^[ \t]*)"ACTIVE_STRUCTURED_INTERACTION": dict\(active_structured_interaction or \{\}\),$', re.MULTILINE)
goal, count = pattern.subn(
    lambda match: (
        f'{match.group("indent")}"ACTIVE_STRUCTURED_INTERACTION": dict(active_structured_interaction or {{}}),\n'
        f'{match.group("indent")}"CANONICAL_SEMANTIC_OUTPUT_VOCABULARY": semantic_vocabulary,'
    ),
    goal,
)
if count < 3:
    raise RuntimeError(f"semantic vocabulary prompt injection: expected at least 3 prompt sites, got {count}")

feedback_anchor = '''    if (
        alignment.verdict == "incomplete"
        and alignment.independent
        and str(alignment.reason_code or "").casefold().startswith("target-scope-constraint")
'''
feedback_block = '''    normalized_reason = str(alignment.reason_code or "").strip().casefold().replace("-", "_").replace(" ", "_")
    requested_output_mismatch = (
        "requested_effect" in normalized_reason
        and any(marker in normalized_reason for marker in ("fidelity", "faithful", "business_effect", "semantic"))
    )
    if alignment.verdict == "incomplete" and alignment.independent and requested_output_mismatch:
        invalid_requested_output_spans = list(dict.fromkeys(
            _clean_text(value, limit=240)
            for value in alignment.missing_spans
            if _clean_text(value, limit=240)
        ))
        if invalid_requested_output_spans:
            return {
                "independent_verifier_feedback": {
                    "authority": "independent_goal_alignment",
                    "required_action": "redeclaration_rederiving_requested_outputs",
                    "violation_field": "requested_effect.requested_outputs",
                    "invalid_requested_output_spans": invalid_requested_output_spans,
                    "constraints": [
                        "rederive_requested_outputs_from_current_user_input_and_capability_independent_semantic_vocabulary",
                        "preserve_goal_inventory_goal_ids_literal_evidence_dependencies_target_identity_and_real_scope_constraints",
                        "use_open_when_no_registered_output_description_exactly_represents_the_requested_user_visible_outcome",
                        "do_not_copy_verifier_replacement_semantic_values_or_consult_capability_availability",
                        "runtime_does_not_auto_rewrite_the_candidate",
                    ],
                }
            }
'''
goal = replace_once(
    goal,
    feedback_anchor,
    feedback_block + feedback_anchor,
    "add requested-output redeclaration feedback",
)
GOAL.write_text(goal, encoding="utf-8")


dialogue = DIALOGUE.read_text(encoding="utf-8")
dialogue = replace_once(
    dialogue,
    '''    if violation_field == "target_candidate.scope_constraints":
        field = violation_field
    elif authority == "candidate_blind_goal_inventory":
''',
    '''    if violation_field == "target_candidate.scope_constraints":
        field = violation_field
    elif violation_field == "requested_effect.requested_outputs":
        field = violation_field
    elif authority == "candidate_blind_goal_inventory":
''',
    "project requested-output violation field",
)
dialogue = replace_once(
    dialogue,
    '    for key in ("uncovered_outcome_spans", "invalid_scope_constraint_spans"):\n',
    '    for key in ("uncovered_outcome_spans", "invalid_scope_constraint_spans", "invalid_requested_output_spans"):\n',
    "project requested-output violation spans",
)
dialogue = replace_once(
    dialogue,
    '''        if field == "target_candidate.scope_constraints":
            writer_constraints.insert(0,
                "remove_only_listed_invalid_scope_constraint_entries_and_preserve_other_literal_population_narrowing_constraints"
            )
''',
    '''        if field == "target_candidate.scope_constraints":
            writer_constraints.insert(0,
                "remove_only_listed_invalid_scope_constraint_entries_and_preserve_other_literal_population_narrowing_constraints"
            )
        elif field == "requested_effect.requested_outputs":
            writer_constraints.insert(0,
                "rederive_requested_outputs_from_current_user_input_and_semantic_vocabulary_use_open_when_no_exact_registered_meaning_exists"
            )
''',
    "project requested-output writer constraint",
)
DIALOGUE.write_text(dialogue, encoding="utf-8")

TEST.write_text('''from __future__ import annotations

from agent_core.lifecycle.dialogue_runtime import _semantic_writer_declaration_result_projection
from agent_core.lifecycle.goal_planning import (
    GoalAlignmentVerdict,
    _alignment_repair_feedback,
    _dependency_blind_goal_projection,
    _semantic_vocabulary_for_alignment,
)


class _Registry:
    def semantic_vocabulary_snapshot(self):
        return {
            "version": "semantic-output-vocabulary@1",
            "authority": "domain_semantics_only_capability_independent",
            "availability_exposed": True,
            "tool_names_exposed": True,
            "outputs": [
                {
                    "output_id": "refund.status",
                    "subject_type": "refund",
                    "effect_kinds": ["read"],
                    "description": "读取已经存在的退款申请记录以及当前处理状态。",
                    "legacy_effect_aliases": ["refund.query_status:refund"],
                    "tool_name": "list_refunds",
                    "available": True,
                },
                {
                    "output_id": "invoice.status",
                    "subject_type": "invoice",
                    "effect_kinds": ["read"],
                    "description": "发票申请或开具状态。",
                },
            ],
        }


def test_alignment_vocabulary_is_meaning_only_and_strips_execution_signals(monkeypatch):
    import agent_core.modules.registry as registry_module

    monkeypatch.setattr(registry_module, "current_module_registry", lambda: _Registry())
    snapshot = _semantic_vocabulary_for_alignment()
    assert snapshot["authority"] == "domain_semantics_only_capability_independent"
    assert snapshot["availability_exposed"] is False
    assert snapshot["tool_names_exposed"] is False
    refund = next(row for row in snapshot["outputs"] if row["output_id"] == "refund.status")
    assert refund == {
        "output_id": "refund.status",
        "subject_type": "refund",
        "effect_kinds": ["read"],
        "description": "读取已经存在的退款申请记录以及当前处理状态。",
    }
    assert "tool_name" not in refund
    assert "available" not in refund
    assert "legacy_effect_aliases" not in refund


def _requested_output_mismatch() -> GoalAlignmentVerdict:
    return GoalAlignmentVerdict(
        "incomplete",
        ("退款什么时候到账",),
        ("退款什么时候到账",),
        "requested_effect_semantic_fidelity",
        "model",
        True,
        {
            "dependency_authority": "independent_goal_alignment",
            "dependency_proof_complete": True,
            "dependency_graph_match": True,
            "dependency_edges": [],
            "verifier_repair_attempted": True,
            "verifier_repair_kind": "candidate_blind_dependency_requested_effect_reaudit",
        },
    )


def test_requested_output_mismatch_becomes_field_specific_redeclaration_feedback():
    row = _alignment_repair_feedback(_requested_output_mismatch())["independent_verifier_feedback"]
    assert row["authority"] == "independent_goal_alignment"
    assert row["required_action"] == "redeclaration_rederiving_requested_outputs"
    assert row["violation_field"] == "requested_effect.requested_outputs"
    assert row["invalid_requested_output_spans"] == ["退款什么时候到账"]
    assert "use_open_when_no_registered_output_description_exactly_represents_the_requested_user_visible_outcome" in row["constraints"]


def test_writer_projection_exposes_only_violation_not_replacement_output_identity():
    alignment = _requested_output_mismatch()
    result = {
        "ok": False,
        "code": "GOAL_DECLARATION_INCOMPLETE",
        "message": "redeclare",
        "data": {
            "alignment_proof": alignment.as_dict(),
            **_alignment_repair_feedback(alignment),
            "current_user_input": "鼠标订单的退款什么时候到账？",
            "repair_contract": {"authority": "current_user_input_only", "required_action": "redeclaration"},
        },
    }
    projected = _semantic_writer_declaration_result_projection(result)
    feedback = projected["data"]["independent_verifier_feedback"]
    assert feedback["authority"] == "read_only_violation_evidence"
    assert feedback["violation"]["field"] == "requested_effect.requested_outputs"
    assert feedback["violation"]["evidence_spans"] == ["退款什么时候到账"]
    assert "rederive_requested_outputs_from_current_user_input_and_semantic_vocabulary_use_open_when_no_exact_registered_meaning_exists" in feedback["constraints"]
    payload = projected["data"]
    assert "alignment_proof" not in payload
    assert "requested_effect" not in payload
    assert "replacement_output_id" not in str(projected)


def test_candidate_blind_audit_keeps_canonical_output_identity_for_exactness_review():
    goals = [{
        "goal_id": "g1",
        "evidence_span": "退款什么时候到账",
        "requested_effect": {
            "domain": "refund",
            "operation": "query_status",
            "object_type": "refund",
            "requested_outputs": [{
                "output_id": "refund.status",
                "evidence_span": "退款什么时候到账",
            }],
        },
        "depends_on": [],
    }]
    projection = _dependency_blind_goal_projection(goals)
    assert projection[0]["requested_effect"]["requested_outputs"] == [{
        "output_id": "refund.status",
        "evidence_span": "退款什么时候到账",
    }]
''', encoding="utf-8")

TRIGGER.write_text(
    "release_request: 2026-08-15T17:26:00+08:00\n"
    "provider: deepseek\n"
    "model: deepseek-v4-flash\n"
    "embedding_model: text-embedding-v4\n"
    "embedding_dimension: 1024\n"
    "reason: rerun protected release after capability-independent requested-output exactness repair\n",
    encoding="utf-8",
)

print("requested-output exactness patch applied")
