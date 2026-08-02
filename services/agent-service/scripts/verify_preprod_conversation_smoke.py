#!/usr/bin/env python3
"""Read-only real-model smoke for independent semantic goal prototypes.

The smoke exercises only the planning protocol.  It never builds the lifecycle
graph, opens a Draft, or dispatches a BusinessPort.  Each real-model
``declare_turn_goals`` call is compared with an independent ``goal_oracle``;
therefore a multi-intent turn fails when the model drops one branch even if the
remaining candidate tool is otherwise allowed.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parents[1]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from agent_core.config import get_model, get_model_profile  # noqa: E402
from agent_core.lifecycle.protocol import planning_schemas  # noqa: E402
from agent_core.lifecycle.goal_planning import validate_goal_declaration  # noqa: E402
from agent_core.model_calls import (  # noqa: E402
    RealModelCertificationError,
    attest_real_model_metadata,
    certification_session_evidence,
    classify_model_failure,
    invoke_model,
    is_environmental_model_failure_category,
    model_call_scope,
    resolve_real_model_identity,
)
from agent_core.runtime.node_support import tool_calls  # noqa: E402
from agent_core.composition import get_runtime_registry  # noqa: E402

CATALOG = WORKSPACE / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"
_SAFE_CASE_ID_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]{0,127}):\s*(.*)$", re.DOTALL)


def _compact_span(value: Any) -> str:
    """Normalize presentation-only differences without rewriting semantics."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s,，。.!！?？;；:：、]+", "", normalized)


def _span_matches_oracle(*, expected: Any, actual: Any) -> bool:
    """Accept a literal model span that adds surrounding user wording.

    Goal spans are independently checked by the production validator to be
    literal substrings of the current user turn.  The oracle therefore owns
    the semantic core, while the model may legitimately include an adjacent
    verb or conjunction (for example ``再看看``).  Containment is deliberately
    one-dimensional; fuzzy similarity and token overlap remain forbidden.
    """

    oracle_span = _compact_span(expected)
    model_span = _compact_span(actual)
    return bool(oracle_span and model_span and (
        oracle_span == model_span
        or oracle_span in model_span
        or model_span in oracle_span
    ))


def _user_turns(case: dict[str, Any]) -> list[str]:
    return [
        str(row.get("text") or "")
        for row in list(case.get("turns") or [])
        if isinstance(row, dict) and row.get("role") == "user" and str(row.get("text") or "")
    ]


def _match_oracle(*, case_id: str, oracle: list[dict[str, Any]], goals: list[dict[str, Any]]) -> None:
    if len(goals) != len(oracle):
        raise RuntimeError(f"{case_id}: goal count mismatch, expected {len(oracle)}, got {len(goals)}")
    goal_ids = [str(row.get("goal_id") or "") for row in goals]
    duplicate_ids = sorted(
        goal_id for goal_id in set(goal_ids) if goal_id and goal_ids.count(goal_id) > 1
    )
    if duplicate_ids:
        raise RuntimeError(
            f"{case_id}: duplicate goal_id values are forbidden: {duplicate_ids}"
        )
    unmatched = list(goals)
    oracle_to_goal: dict[str, str] = {}
    for expected in oracle:
        evidence = str(expected.get("evidence_span") or "")
        goal_type = str(expected.get("goal_type") or "")
        required = bool(expected.get("required", True))
        exact_matches = [
            row for row in unmatched
            if str(row.get("evidence_span") or "") == evidence
            and str(row.get("goal_type") or "") == goal_type
            and bool(row.get("required", True)) == required
        ]
        matches = exact_matches or [
            row for row in unmatched
            if _span_matches_oracle(
                expected=evidence,
                actual=row.get("evidence_span"),
            )
            and str(row.get("goal_type") or "") == goal_type
            and bool(row.get("required", True)) == required
        ]
        if len(matches) != 1:
            raise RuntimeError(f"{case_id}: no unique model goal matches oracle span={evidence!r}, type={goal_type!r}")
        matched = matches[0]
        oracle_id = str(expected.get("oracle_id") or "")
        goal_id = str(matched.get("goal_id") or "")
        if not oracle_id or not goal_id:
            raise RuntimeError(f"{case_id}: oracle and model goals must declare stable IDs")
        oracle_to_goal[oracle_id] = goal_id
        unmatched.remove(matched)
    if unmatched:
        raise RuntimeError(f"{case_id}: model emitted undeclared extra goals")
    goals_by_id = {str(row.get("goal_id") or ""): row for row in goals}
    for expected in oracle:
        expected_dependencies = {
            oracle_to_goal.get(str(value), "<unmatched>")
            for value in expected.get("depends_on") or []
        }
        goal = goals_by_id[oracle_to_goal[str(expected["oracle_id"])]]
        actual_dependencies = {str(value) for value in goal.get("depends_on") or []}
        if actual_dependencies != expected_dependencies:
            raise RuntimeError(
                f"{case_id}: goal dependency mismatch for oracle {expected['oracle_id']}"
            )


def _validate_with_production_goal_contract(
    *, case_id: str, user_text: str, goals: list[dict[str, Any]]
) -> dict[str, Any]:
    result, declared = validate_goal_declaration(
        state={"current_user_input": user_text},
        args={"goals": goals},
        capability_registry=get_runtime_registry().capabilities,
    )
    if not result.get("ok") or declared is None:
        errors = (result.get("data") or {}).get("errors") or [result.get("code")]
        raise RuntimeError(
            f"{case_id}: production goal declaration rejected model output: {errors}"
        )
    return declared


def _identity_failure_reason(exc: RealModelCertificationError) -> str:
    if exc.environment_blocked:
        return "real_model_environment_unavailable"
    if exc.phase == "response":
        return "real_model_response_attestation_invalid"
    return "real_model_identity_invalid"


def _safe_semantic_failure(exc: Exception) -> dict[str, str]:
    """Classify a semantic failure without retaining prompts or model output."""

    message = str(exc or "")
    case_id = ""
    detail = message
    case_match = _SAFE_CASE_ID_RE.match(message)
    if case_match:
        case_id = case_match.group(1)
        detail = case_match.group(2)

    patterns = (
        ("goal count mismatch", "goal_count_mismatch"),
        ("duplicate goal_id values", "duplicate_goal_ids"),
        ("no unique model goal matches oracle", "oracle_goal_match_failed"),
        ("oracle and model goals must declare stable IDs", "stable_goal_id_missing"),
        ("model emitted undeclared extra goals", "undeclared_extra_goals"),
        ("goal dependency mismatch", "goal_dependency_mismatch"),
        ("production goal declaration rejected model output", "production_goal_contract_rejected"),
        ("model did not emit exactly one declare_turn_goals call", "declare_turn_goals_call_invalid"),
        ("expected exactly 12 semantic prototypes", "prototype_catalog_count_invalid"),
        ("preproduction semantic prototypes must currently be single-turn", "prototype_turn_count_invalid"),
    )
    failure_code = next((code for marker, code in patterns if marker in detail), "semantic_component_exception")
    error_code = f"semantic_{failure_code}"
    if case_id:
        error_code = f"{error_code}__{case_id}"
    return {
        "error_code": error_code,
        "failure_code": failure_code,
        **({"failed_case_id": case_id} if case_id else {}),
    }


def main() -> int:
    try:
        identity = resolve_real_model_identity()
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
        cases = [
            row for row in payload.get("cases") or []
            if isinstance(row, dict)
            and isinstance(row.get("execution_contract"), dict)
            and row["execution_contract"].get("preproduction_risk_prototype") is True
        ]
        if len(cases) != 12:
            raise RuntimeError(f"expected exactly 12 semantic prototypes, got {len(cases)}")
        if any(len(_user_turns(case)) != 1 for case in cases):
            raise RuntimeError("preproduction semantic prototypes must currently be single-turn")

        model = get_model()
        bound = model.bind_tools(planning_schemas()) if hasattr(model, "bind_tools") else model
        evidence: list[dict[str, Any]] = []
        system = SystemMessage(content=(
            "只执行目标声明：调用 declare_turn_goals，完整保留用户的每一个目标、条件和依赖。"
            "不能把不支持分支吞掉，也不能用相似能力代替。evidence_span 必须来自用户原话。"
        ))
        # Protected mode uses the production independent goal-alignment model,
        # so each prototype may consume one declaration call and one verifier call.
        with model_call_scope(max_calls=24, scope="preprod_semantic_goal_prototypes") as calls:
            for case in cases:
                turn = case["execution_contract"]["turn_contracts"][0]
                response, trace = invoke_model(
                    purpose=f"preprod_semantic_goal:{case['id']}",
                    model=bound,
                    payload=[system, HumanMessage(content=str(turn["user_text"]))],
                )
                attestation = attest_real_model_metadata(
                    response=response,
                    identity=identity,
                )
                candidates = tool_calls(response)
                if len(candidates) != 1 or str(candidates[0].get("name") or "") != "declare_turn_goals":
                    raise RuntimeError(f"{case['id']}: model did not emit exactly one declare_turn_goals call")
                args = candidates[0].get("args") if isinstance(candidates[0].get("args"), dict) else {}
                goals = [row for row in list(args.get("goals") or []) if isinstance(row, dict)]
                oracle = [row for row in list(turn.get("goal_oracle") or []) if isinstance(row, dict)]
                _match_oracle(case_id=case["id"], oracle=oracle, goals=goals)
                declared = _validate_with_production_goal_contract(
                    case_id=str(case["id"]),
                    user_text=str(turn["user_text"]),
                    goals=goals,
                )
                evidence.append({
                    "case_id": case["id"],
                    "goal_count": len(goals),
                    "goal_types": [str(row.get("goal_type") or "") for row in goals],
                    "oracle_required_tools": sorted({
                        str(value)
                        for row in oracle
                        for value in row.get("required_tools") or []
                        if str(value)
                    }),
                    "production_goal_ids": [
                        str(row.get("goal_id") or "")
                        for row in declared.get("goals") or []
                    ],
                    "trace": trace,
                    "provider_attestation": attestation,
                })
        print(json.dumps({
            "status": "PASS",
            "prototype_count": len(cases),
            "identity": identity,
            "certification_session": certification_session_evidence(component="semantic", identity=identity),
            "model_profile": get_model_profile(),
            "calls": calls.summary(),
            "cases": evidence,
            "guarantee": (
                "schema-compliant real-model goal declaration, production validation, and dependency semantics; "
                "tool authorization remains covered by deterministic runtime gates"
            ),
        }, ensure_ascii=False))
        return 0
    except RealModelCertificationError as exc:
        print(json.dumps({
            "status": "BLOCKED_BY_ENVIRONMENT" if exc.environment_blocked else "FAIL",
            "error_type": exc.__class__.__name__,
            "error_code": exc.code,
            "reason": _identity_failure_reason(exc),
        }, ensure_ascii=False))
        return 78 if exc.environment_blocked else 1
    except Exception as exc:
        category = classify_model_failure(exc)
        environment_blocked = is_environmental_model_failure_category(category)
        diagnostic = _safe_semantic_failure(exc)
        if environment_blocked:
            diagnostic["error_code"] = f"semantic_model_environment__{category}"
        print(json.dumps({
            "status": "BLOCKED_BY_ENVIRONMENT" if environment_blocked else "FAIL",
            "error_type": exc.__class__.__name__,
            "error_category": category,
            "reason": "configured_model_environment_unavailable" if environment_blocked else "semantic_prototype_certification_failed",
            **diagnostic,
        }, ensure_ascii=False))
        return 78 if environment_blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())
