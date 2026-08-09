#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path("candidate").resolve()


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}: found {count} for {old[:180]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


planning = ROOT / "services/agent-service/src/agent_core/lifecycle/goal_planning.py"
replace_once(
    planning,
    '''    if verdict == "exact" and not evidence:
        return GoalAlignmentVerdict(
            "indeterminate",
            (),
            (),
            "goal_alignment_evidence_not_in_current_user_text",
            result_source,
            result_independent,
            {**details, "original_verdict": verdict},
        )
    if verdict == "incomplete" and not missing:
        return GoalAlignmentVerdict(
            "indeterminate",
            evidence,
            (),
            "goal_alignment_missing_span_not_grounded",
            result_source,
            result_independent,
            {**details, "original_verdict": verdict},
        )
''',
    '''    if verdict == "exact" and not evidence:
        return GoalAlignmentVerdict(
            "indeterminate",
            (),
            (),
            "goal_alignment_evidence_not_in_current_user_text",
            result_source,
            result_independent,
            {
                **details,
                "original_verdict": verdict,
                "grounding_failure": "evidence_spans",
            },
        )
    if verdict == "incomplete" and not missing:
        return GoalAlignmentVerdict(
            "indeterminate",
            evidence,
            (),
            "goal_alignment_missing_span_not_grounded",
            result_source,
            result_independent,
            {
                **details,
                "original_verdict": verdict,
                "grounding_failure": "missing_spans",
            },
        )
''',
)
replace_once(
    planning,
    '''        verifier_repair: str | None = None
        last_indeterminate = GoalAlignmentVerdict(
            "indeterminate", (), (), "goal_alignment_unverified", "model", True, {}
        )
        for attempt in range(2):
''',
    '''        verifier_repair: str | None = None
        verifier_repair_kind: str | None = None
        last_indeterminate = GoalAlignmentVerdict(
            "indeterminate", (), (), "goal_alignment_unverified", "model", True, {}
        )
        for attempt in range(2):
''',
)
replace_once(
    planning,
    '''            else:
                verdict = _as_alignment_verdict(parsed, user_text=user_text, source="model", independent=True)
            if verdict.verdict in {"exact", "incomplete"}:
                return verdict
''',
    '''            else:
                verdict = _as_alignment_verdict(parsed, user_text=user_text, source="model", independent=True)
            if attempt > 0 and verifier_repair_kind:
                verdict = GoalAlignmentVerdict(
                    verdict.verdict,
                    verdict.evidence_spans,
                    verdict.missing_spans,
                    verdict.reason_code,
                    verdict.source,
                    verdict.independent,
                    {
                        **verdict.details,
                        "verifier_repair_attempted": True,
                        "verifier_repair_kind": verifier_repair_kind,
                    },
                )
            if verdict.verdict in {"exact", "incomplete"}:
                return verdict
''',
)
replace_once(
    planning,
    '''                if attempt == 0:
                    verifier_repair = (
                        "Re-audit only the semantic outcome boundary. Clarification is admissible only if USER_TEXT "
''',
    '''                if attempt == 0:
                    verifier_repair_kind = "semantic_scope_reaudit"
                    verifier_repair = (
                        "Re-audit only the semantic outcome boundary. Clarification is admissible only if USER_TEXT "
''',
)
replace_once(
    planning,
    '''            if attempt == 0:
                verifier_repair = (
                    "The previous verifier response did not satisfy the machine-readable JSON contract. "
                    "Return exactly one JSON object using only verdict, evidence_spans, missing_spans and reason_code; "
                    "all spans must be literal substrings of USER_TEXT. Do not change or expand the semantic task."
                )
        return last_indeterminate
''',
    '''            if attempt == 0:
                original_verdict = str(verdict.details.get("original_verdict") or "")
                if (
                    verdict.reason_code == "goal_alignment_missing_span_not_grounded"
                    and original_verdict == "incomplete"
                ):
                    verifier_repair_kind = "incomplete_claim_grounding_reaudit"
                    verifier_repair = (
                        "Re-audit the previous incomplete claim from scratch against the same USER_TEXT and DECLARED_GOALS. "
                        "The prior claim did not identify any machine-grounded omitted outcome, so it is not authoritative. "
                        "If the declaration is truly incomplete, copy every omitted user-observable outcome into missing_spans "
                        "as an exact literal contiguous substring of USER_TEXT. Do not paraphrase, infer a hidden prerequisite, "
                        "invent a target-resolution step, or use tool/capability/oracle knowledge. If no literal omitted outcome "
                        "can be identified after re-audit, withdraw the incomplete claim and return exact with literal "
                        "evidence_spans. Return only verdict, evidence_spans, missing_spans and reason_code."
                    )
                elif (
                    verdict.reason_code == "goal_alignment_evidence_not_in_current_user_text"
                    and original_verdict == "exact"
                ):
                    verifier_repair_kind = "exact_claim_grounding_reaudit"
                    verifier_repair = (
                        "Re-audit the previous exact claim against the same USER_TEXT and DECLARED_GOALS. The prior exact "
                        "claim lacked machine-grounded evidence. If exact, copy literal contiguous USER_TEXT spans that cover "
                        "the preserved requested outcomes into evidence_spans. If it is not exact, return incomplete or clarify "
                        "only with the normal strict contract; any missing_spans must be literal USER_TEXT substrings. Do not "
                        "use tool/capability/oracle knowledge. Return only verdict, evidence_spans, missing_spans and reason_code."
                    )
                else:
                    verifier_repair_kind = "machine_format_repair"
                    verifier_repair = (
                        "The previous verifier response did not satisfy the machine-readable JSON contract. "
                        "Return exactly one JSON object using only verdict, evidence_spans, missing_spans and reason_code; "
                        "all spans must be literal substrings of USER_TEXT. Do not change or expand the semantic task."
                    )
        return last_indeterminate
''',
)

smoke = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
replace_once(
    smoke,
    '''        diagnostic["alignment"] = {
            "verdict": str(alignment.get("verdict") or ""),
            "reason_code": str(alignment.get("reason_code") or ""),
            "source": str(alignment.get("source") or ""),
            "independent": bool(alignment.get("independent")),
        }
''',
    '''        alignment_details = alignment.get("details") if isinstance(alignment.get("details"), dict) else {}
        diagnostic["alignment"] = {
            "verdict": str(alignment.get("verdict") or ""),
            "reason_code": str(alignment.get("reason_code") or ""),
            "source": str(alignment.get("source") or ""),
            "independent": bool(alignment.get("independent")),
            "evidence_spans": [
                str(value) for value in list(alignment.get("evidence_spans") or []) if str(value)
            ][:8],
            "missing_spans": [
                str(value) for value in list(alignment.get("missing_spans") or []) if str(value)
            ][:8],
            "original_verdict": str(alignment_details.get("original_verdict") or ""),
            "grounding_failure": str(alignment_details.get("grounding_failure") or ""),
            "verifier_repair_attempted": bool(alignment_details.get("verifier_repair_attempted")),
            "verifier_repair_kind": str(alignment_details.get("verifier_repair_kind") or ""),
        }
''',
)
replace_once(
    smoke,
    '''        # Each accepted declaration is checked by both independent model validators
        # (alignment + candidate-blind granularity). A rejected declaration may be repaired
        # once through the exact same protected path. Each independent verifier may use one
        # format-only repair, so the worst-case envelope is 12 * 2 * (1 declaration + 2 alignment + 2 granularity) = 120.
''',
    '''        # Each accepted declaration is checked by both independent model validators
        # (alignment + candidate-blind granularity). A rejected declaration may be repaired
        # once through the exact same protected path. Each independent verifier remains capped
        # at two calls; alignment may spend its existing second call on format/grounding re-audit,
        # so the worst-case envelope stays 12 * 2 * (1 declaration + 2 alignment + 2 granularity) = 120.
''',
)

print("post-attempt8 bounded alignment grounding re-audit repair applied")
