from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "services/agent-service/src/agent_core/runtime/semantic_capability_verifier.py"
GATE = ROOT / "services/agent-service/src/agent_core/runtime/capability_gate.py"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_first(text: str, old: str, new: str, *, label: str) -> str:
    if old not in text:
        raise SystemExit(f"{label}: anchor not found")
    return text.replace(old, new, 1)


text = PATH.read_text(encoding="utf-8")

text = replace_once(
    text,
    """        verified_context: list[dict[str, Any]],\n        step_context: dict[str, Any] | None = None,\n    ) -> SemanticVerdict | dict[str, Any]: ...\n""",
    """        verified_context: list[dict[str, Any]],\n        step_context: dict[str, Any] | None = None,\n        deterministic_target_proof: dict[str, Any] | None = None,\n    ) -> SemanticVerdict | dict[str, Any]: ...\n""",
    label="protocol-target-proof",
)

insert_anchor = """class ModelSemanticCapabilityVerifier:\n"""
helper = r'''
def _project_candidate_arguments(
    args: dict[str, Any],
    deterministic_target_proof: dict[str, Any] | None,
) -> dict[str, Any]:
    """Hide opaque handle identity once Runtime has already proved historical binding.

    The second model remains responsible for semantic effect/condition exactness,
    but it must not become a second target resolver. Handle membership, recency
    and frozen historical-reference identity are owned by CapabilityGate's
    deterministic proofs. We preserve target operators and user-bound values,
    replacing only opaque handles with an explicit Runtime-owned marker.
    """
    projected = dict(args or {})
    proof = deterministic_target_proof if isinstance(deterministic_target_proof, dict) else {}
    if proof.get("historical_reference_binding_authoritative") is not True:
        return projected
    target = projected.get("target") if isinstance(projected.get("target"), dict) else None
    if target is None:
        return projected
    target_projection = dict(target)
    for key in ("left_handle", "right_handle", "source_handle"):
        if str(target_projection.get(key) or "").strip():
            target_projection[key] = "<runtime-proven-opaque-handle>"
    projected["target"] = target_projection
    return projected


def _apply_deterministic_target_authority(
    verdict: SemanticVerdict,
    *,
    user_text: str,
    deterministic_target_proof: dict[str, Any] | None,
    step_context: dict[str, Any] | None,
) -> SemanticVerdict:
    """Prevent a second model from overruling a completed Runtime target proof.

    This is intentionally narrow. A non-exact verdict is converted only when
    the verifier itself says the *only* mismatch dimension is ``target`` and
    CapabilityGate has already frozen an authoritative historical-reference
    binding. Effect/condition/other mismatches remain fail-closed.
    """
    proof = deterministic_target_proof if isinstance(deterministic_target_proof, dict) else {}
    if proof.get("historical_reference_binding_authoritative") is not True:
        return verdict
    if verdict.verdict not in {"unsupported", "clarify"}:
        return verdict
    dimensions = {
        str(value).strip().lower()
        for value in list((verdict.details or {}).get("mismatch_dimensions") or [])
        if str(value).strip()
    }
    if dimensions != {"target"}:
        return verdict
    evidence = verdict.evidence_span if verdict.evidence_span and verdict.evidence_span in user_text else ""
    if not evidence:
        for goal in list((step_context or {}).get("declared_goals") or []):
            if not isinstance(goal, dict):
                continue
            span = str(goal.get("evidence_span") or "").strip()
            if span and span in user_text:
                evidence = span
                break
    if not evidence:
        return SemanticVerdict(
            "indeterminate", "", "runtime_target_authority_lacks_literal_semantic_evidence",
            verdict.source, verdict.independent,
            {**dict(verdict.details), "runtime_target_authority_applied": False},
        )
    return SemanticVerdict(
        "exact",
        evidence,
        "runtime_target_authority_superseded_second_model_target_rejudgment",
        verdict.source,
        verdict.independent,
        {
            **dict(verdict.details),
            "runtime_target_authority_applied": True,
            "target_authority": "capability_gate_deterministic_reference_binding",
            "original_verdict": verdict.verdict,
            "original_reason_code": verdict.reason_code,
        },
    )


'''
text = replace_once(text, insert_anchor, helper + insert_anchor, label="insert-target-authority-helpers")

# The first concrete SemanticVerdict signature after the protocol is the model
# verifier; CandidateOnly is patched later with its more specific body anchor.
text = replace_first(
    text,
    """        verified_context: list[dict[str, Any]],\n        step_context: dict[str, Any] | None = None,\n    ) -> SemanticVerdict:\n""",
    """        verified_context: list[dict[str, Any]],\n        step_context: dict[str, Any] | None = None,\n        deterministic_target_proof: dict[str, Any] | None = None,\n    ) -> SemanticVerdict:\n""",
    label="model-signature",
)

text = replace_once(
    text,
    """                \"Return JSON only with verdict (exact|clarify|unsupported), evidence_span, reason_code.\"\n            )\n""",
    """                \"When RUNTIME_TARGET_BINDING_PROOF.historical_reference_binding_authoritative is true, Runtime has already proved the exact historical ResultRef/member binding, recency and target scope; do not reinterpret opaque handles and do not return target mismatch for that binding. Judge only semantic dimensions not already owned by that proof. \"\n                \"Return JSON only with verdict (exact|clarify|unsupported), evidence_span, reason_code, mismatch_dimensions. mismatch_dimensions is an array drawn only from target|effect|condition|other; use [] for exact.\"\n            )\n""",
    label="model-instruction",
)

text = replace_once(
    text,
    """            \"evidence_span must be an exact substring of USER_TEXT_UNTRUSTED\",\n        ]\n""",
    """            \"when RUNTIME_TARGET_BINDING_PROOF says historical_reference_binding_authoritative=true, target identity/member/recency/scope is a trusted Runtime fact; do not use target as a mismatch dimension, and do not compare redacted opaque handles with labels or ResultRefs\",\n            \"mismatch_dimensions must identify every remaining reason for a non-exact verdict; a target-only mismatch is invalid when Runtime target authority is true\",\n            \"evidence_span must be an exact substring of USER_TEXT_UNTRUSTED\",\n        ]\n""",
    label="decision-rules",
)

text = replace_once(
    text,
    """                \"arguments\": args,\n            },\n            \"VERIFIED_CONTEXT_SUMMARY\": verified_context,\n            \"DECLARED_WORKFLOW_STEP\": dict(step_context or {}),\n        }\n""",
    """                \"arguments\": _project_candidate_arguments(args, deterministic_target_proof),\n            },\n            \"VERIFIED_CONTEXT_SUMMARY\": verified_context,\n            \"DECLARED_WORKFLOW_STEP\": dict(step_context or {}),\n            \"RUNTIME_TARGET_BINDING_PROOF\": dict(deterministic_target_proof or {}),\n        }\n""",
    label="prompt-target-proof",
)

text = replace_once(
    text,
    """            if parsed is None:\n                return SemanticVerdict(\"indeterminate\", \"\", \"semantic_verifier_non_json\", \"model\", True, {})\n            return _as_verdict(parsed, user_text=user_text, source=\"model\", independent=True)\n""",
    """            if parsed is None:\n                return SemanticVerdict(\"indeterminate\", \"\", \"semantic_verifier_non_json\", \"model\", True, {})\n            details = dict(parsed.get(\"details\") or {}) if isinstance(parsed.get(\"details\"), dict) else {}\n            if isinstance(parsed.get(\"mismatch_dimensions\"), list):\n                details[\"mismatch_dimensions\"] = [\n                    str(value).strip().lower()\n                    for value in list(parsed.get(\"mismatch_dimensions\") or [])\n                    if str(value).strip()\n                ]\n            parsed = {**parsed, \"details\": details}\n            verdict = _as_verdict(parsed, user_text=user_text, source=\"model\", independent=True)\n            return _apply_deterministic_target_authority(\n                verdict,\n                user_text=user_text,\n                deterministic_target_proof=deterministic_target_proof,\n                step_context=step_context,\n            )\n""",
    label="postprocess-target-verdict",
)

# Candidate-only verifier signature: keep local behavior, ignore the new proof.
text = replace_once(
    text,
    """        verified_context: list[dict[str, Any]],\n        step_context: dict[str, Any] | None = None,\n    ) -> SemanticVerdict:\n        del step_context\n""",
    """        verified_context: list[dict[str, Any]],\n        step_context: dict[str, Any] | None = None,\n        deterministic_target_proof: dict[str, Any] | None = None,\n    ) -> SemanticVerdict:\n        del step_context, deterministic_target_proof\n""",
    label="candidate-signature",
)

text = replace_once(
    text,
    """    contract: ToolCapabilityContract,\n    effect_id: str = \"\",\n) -> SemanticVerdict:\n""",
    """    contract: ToolCapabilityContract,\n    effect_id: str = \"\",\n    deterministic_target_proof: dict[str, Any] | None = None,\n) -> SemanticVerdict:\n""",
    label="entry-signature",
)

text = replace_once(
    text,
    """        verified_context=context,\n        step_context=step_context,\n    )\n""",
    """        verified_context=context,\n        step_context=step_context,\n        deterministic_target_proof=deterministic_target_proof,\n    )\n""",
    label="entry-pass-proof",
)

PATH.write_text(text, encoding="utf-8")


gate = GATE.read_text(encoding="utf-8")
anchor = """def issue_execution_permit(\n"""
helper_gate = r'''
def _deterministic_semantic_target_proof(
    *,
    normalized_args: dict[str, Any],
    visible_reference: dict[str, Any],
    semantic_reference_binding: dict[str, Any],
    member_scope: dict[str, Any],
    derived_scope: dict[str, Any],
) -> dict[str, Any]:
    """Project the Runtime-owned target facts that a semantic model must not re-resolve."""
    checks = [
        row for row in list(semantic_reference_binding.get("checks") or [])
        if isinstance(row, dict)
    ]
    historical_required = any(bool(row.get("required")) for row in checks)
    authoritative = bool(
        historical_required
        and visible_reference.get("complete")
        and semantic_reference_binding.get("complete")
        and member_scope.get("complete")
        and derived_scope.get("complete")
    )
    target = normalized_args.get("target") if isinstance(normalized_args.get("target"), dict) else {}
    return {
        "version": "deterministic-semantic-target-proof@1",
        "authority": "capability_gate_target_binding_only",
        "historical_reference_binding_required": historical_required,
        "historical_reference_binding_authoritative": authoritative,
        "target_mode": str(target.get("mode") or "") or None,
        "visible_reference_complete": bool(visible_reference.get("complete")),
        "semantic_reference_binding_complete": bool(semantic_reference_binding.get("complete")),
        "explicit_member_scope_complete": bool(member_scope.get("complete")),
        "derived_collection_scope_complete": bool(derived_scope.get("complete")),
        "goal_checks": [
            {
                "goal_id": str(row.get("goal_id") or ""),
                "required": bool(row.get("required")),
                "matched": bool(row.get("matched")),
                "reason_code": str(row.get("reason_code") or ""),
                "expected_cardinality": str(row.get("expected_cardinality") or ""),
            }
            for row in checks
        ],
        "opaque_handle_identity_exposed_to_semantic_model": False if authoritative else True,
        "mutates_target": False,
        "creates_execution_permit": False,
    }


'''
gate = replace_once(gate, anchor, helper_gate + anchor, label="insert-gate-target-proof")

gate = replace_once(
    gate,
    """    semantic = (\n        verify_candidate_semantics(\n            state=state, tool_name=tool_name, args=normalized_args,\n            contract=contract, effect_id=effect_id,\n        )\n""",
    """    deterministic_target_proof = _deterministic_semantic_target_proof(\n        normalized_args=normalized_args,\n        visible_reference=visible_reference,\n        semantic_reference_binding=semantic_reference_binding,\n        member_scope=member_scope,\n        derived_scope=derived_scope,\n    )\n    semantic = (\n        verify_candidate_semantics(\n            state=state, tool_name=tool_name, args=normalized_args,\n            contract=contract, effect_id=effect_id,\n            deterministic_target_proof=deterministic_target_proof,\n        )\n""",
    label="gate-pass-target-proof",
)

gate = replace_once(
    gate,
    """        \"semantic_reference_binding\": semantic_reference_binding,\n        \"explicit_member_scope\": member_scope,\n""",
    """        \"semantic_reference_binding\": semantic_reference_binding,\n        \"deterministic_semantic_target_proof\": deterministic_target_proof,\n        \"explicit_member_scope\": member_scope,\n""",
    label="gate-record-target-proof",
)

GATE.write_text(gate, encoding="utf-8")
print("Attempt-3 deterministic target-authority repair applied")
