from __future__ import annotations

import argparse
import json
from pathlib import Path

CHANGE_ID = "repair-stage4-2-dependency-obligation-evidence-pipeline"
PATH = Path("services/agent-service/src/agent_core/lifecycle/goal_planning.py")

IMPORT_OLD = '''from agent_core.goal_graph.dependency_alignment import (
    alignment_dependency_authority_details,
    apply_alignment_dependency_proof,
    dependency_authority_closed_and_matching,
)
'''
IMPORT_NEW = '''from agent_core.goal_graph.dependency_alignment import (
    COUNTERFACTUAL_EVIDENCE_CONTRACT,
    DEPENDENCY_OBLIGATION_EVIDENCE_CONTRACT,
    DEPENDENCY_OBLIGATION_EVIDENCE_PRODUCER,
    TARGET_COMPATIBILITY_EVIDENCE_CONTRACT,
    alignment_dependency_authority_details,
    alignment_dependency_premise_digest,
    apply_alignment_dependency_proof,
    dependency_authority_closed_and_matching,
)
'''

HELPER_MARKER = '\n\ndef _dependency_blind_goal_projection('
HELPER = r'''


def _validated_dependency_obligation_evidence(
    *,
    user_text: str,
    goals: list[dict[str, Any]],
    verdict: GoalAlignmentVerdict,
    phase: str,
) -> dict[str, Any]:
    """Materialize obligation evidence from the already-validated blind audit.

    This function never reads arbitrary model obligation fields.  Its input pair
    rows are the normalized output of ``_model_alignment_pairwise_dependency_proof``
    and this helper is called only from the candidate-blind semantic verifier
    path whose rules require requested-effect/target-scope fidelity plus the
    current-turn result-removal counterfactual for every unordered pair.
    """

    details = verdict.details if isinstance(verdict.details, dict) else {}
    rows = details.get("dependency_pair_decisions")
    if details.get("dependency_proof_complete") is not True or not isinstance(rows, list):
        return {}

    premise_digest = alignment_dependency_premise_digest(
        user_text=user_text,
        goals=goals,
    )
    goal_by_id = {
        str(goal.get("goal_id") or ""): goal
        for goal in goals
        if isinstance(goal, dict) and str(goal.get("goal_id") or "")
    }
    target_compatible = bool(
        verdict.exact
        or (
            verdict.reason_code == "goal_alignment_dependency_graph_mismatch"
            and not verdict.missing_spans
        )
    )
    pair_evidence: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        goal_a_id = _clean_text(raw.get("goal_a_id"), limit=200)
        goal_b_id = _clean_text(raw.get("goal_b_id"), limit=200)
        relation = _clean_text(raw.get("relation"), limit=80).lower()
        if not goal_a_id or not goal_b_id or not relation:
            continue
        goal_a = goal_by_id.get(goal_a_id) or {}
        goal_b = goal_by_id.get(goal_b_id) or {}
        target_record: dict[str, Any] = {
            "contract": TARGET_COMPATIBILITY_EVIDENCE_CONTRACT,
            "result": "PASS" if target_compatible else "UNKNOWN",
            "evidence": {
                "verifier_phase": str(phase or "candidate_blind_dependency_reaudit"),
                "alignment_verdict": verdict.verdict,
                "alignment_reason_code": verdict.reason_code,
                "evidence_spans": list(verdict.evidence_spans),
                "missing_spans": list(verdict.missing_spans),
                "goal_a_semantics": {
                    "requested_effect": deepcopy(goal_a.get("requested_effect"))
                    if isinstance(goal_a.get("requested_effect"), dict)
                    else None,
                    "target_candidate": deepcopy(goal_a.get("target_candidate"))
                    if isinstance(goal_a.get("target_candidate"), dict)
                    else None,
                },
                "goal_b_semantics": {
                    "requested_effect": deepcopy(goal_b.get("requested_effect"))
                    if isinstance(goal_b.get("requested_effect"), dict)
                    else None,
                    "target_candidate": deepcopy(goal_b.get("target_candidate"))
                    if isinstance(goal_b.get("target_candidate"), dict)
                    else None,
                },
            },
        }
        counterfactual_record = {
            "contract": COUNTERFACTUAL_EVIDENCE_CONTRACT,
            "result": "PASS",
            "evidence": {
                "verifier_phase": str(phase or "candidate_blind_dependency_reaudit"),
                "counterfactual_rule": "remove-only-earlier-current-turn-user-visible-result-payload",
                "pair_decision": deepcopy(raw),
                "basis_kind": _clean_text(raw.get("basis_kind"), limit=80) or None,
                "basis_span": _clean_text(raw.get("basis_span"), limit=240) or None,
                "proof_source": "normalized_candidate_blind_pairwise_semantic_audit",
            },
        }
        pair_evidence.append(
            {
                "goal_a_id": goal_a_id,
                "goal_b_id": goal_b_id,
                "relation": relation,
                "premise_digest": premise_digest,
                "target_compatibility": target_record,
                "counterfactual": counterfactual_record,
            }
        )
    return {
        "contract": DEPENDENCY_OBLIGATION_EVIDENCE_CONTRACT,
        "producer": DEPENDENCY_OBLIGATION_EVIDENCE_PRODUCER,
        "premise_digest": premise_digest,
        "phase": str(phase or "candidate_blind_dependency_reaudit"),
        "pairs": pair_evidence,
    }
'''

BLOCK_OLD = '''            if (
                blind_dependency_audit
                and not semantic_claim_reaudit
                and isinstance(verdict.details, dict)
                and verdict.details.get("dependency_proof_complete") is True
                and isinstance(verdict.details.get("dependency_pair_decisions"), list)
            ):
                dependency_proof_ledger, _dependency_graph_snapshot = apply_alignment_dependency_proof(
                    dependency_proof_ledger,
                    user_text=user_text,
                    goals=goals,
                    details=verdict.details,
                    phase=str(verifier_repair_kind or "candidate_blind_dependency_reaudit"),
                )
                dependency_authority_snapshot = alignment_dependency_authority_details(
                    dependency_proof_ledger,
                    goals=goals,
                )
                verdict = GoalAlignmentVerdict(
                    verdict.verdict,
                    verdict.evidence_spans,
                    verdict.missing_spans,
                    verdict.reason_code,
                    verdict.source,
                    verdict.independent,
                    {**verdict.details, **dependency_authority_snapshot},
                )
'''

BLOCK_NEW = '''            if (
                blind_dependency_audit
                and not semantic_claim_reaudit
                and isinstance(verdict.details, dict)
                and verdict.details.get("dependency_proof_complete") is True
                and isinstance(verdict.details.get("dependency_pair_decisions"), list)
            ):
                dependency_phase = str(
                    verifier_repair_kind or "candidate_blind_dependency_reaudit"
                )
                obligation_evidence = _validated_dependency_obligation_evidence(
                    user_text=user_text,
                    goals=goals,
                    verdict=verdict,
                    phase=dependency_phase,
                )
                verdict = GoalAlignmentVerdict(
                    verdict.verdict,
                    verdict.evidence_spans,
                    verdict.missing_spans,
                    verdict.reason_code,
                    verdict.source,
                    verdict.independent,
                    {
                        **verdict.details,
                        "dependency_obligation_evidence": obligation_evidence,
                    },
                )
                dependency_proof_ledger, _dependency_graph_snapshot = apply_alignment_dependency_proof(
                    dependency_proof_ledger,
                    user_text=user_text,
                    goals=goals,
                    details=verdict.details,
                    phase=dependency_phase,
                )
                dependency_authority_snapshot = alignment_dependency_authority_details(
                    dependency_proof_ledger,
                    goals=goals,
                )
                verdict = GoalAlignmentVerdict(
                    verdict.verdict,
                    verdict.evidence_spans,
                    verdict.missing_spans,
                    verdict.reason_code,
                    verdict.source,
                    verdict.independent,
                    {**verdict.details, **dependency_authority_snapshot},
                )
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    active = json.loads((root / "governance/active-change.json").read_text(encoding="utf-8"))
    if active.get("change_id") != CHANGE_ID:
        raise SystemExit("wrong active change")
    if active.get("status") != "implementing" or active.get("writer_role") != "product-implementer":
        raise SystemExit("Stage 4.2 product write is not currently authorized")
    if PATH.as_posix() not in list(active.get("allowed_paths") or []):
        raise SystemExit("goal_planning.py is outside the active ChangePermit")

    path = root / PATH
    text = path.read_text(encoding="utf-8")
    if "DEPENDENCY_OBLIGATION_EVIDENCE_PRODUCER" in text:
        print("goal_planning Stage 4.2 producer already applied")
        return 0
    text = replace_once(text, IMPORT_OLD, IMPORT_NEW, "dependency alignment import")
    if text.count(HELPER_MARKER) != 1:
        raise SystemExit(f"helper marker count={text.count(HELPER_MARKER)}")
    text = text.replace(HELPER_MARKER, HELPER + HELPER_MARKER, 1)
    text = replace_once(text, BLOCK_OLD, BLOCK_NEW, "dependency bridge application block")
    path.write_text(text, encoding="utf-8")
    print(path.relative_to(root).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
