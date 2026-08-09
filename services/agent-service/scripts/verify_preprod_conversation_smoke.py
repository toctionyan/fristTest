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
from agent_core.runtime.capability_effects import capability_effect_index  # noqa: E402

CATALOG = WORKSPACE / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"


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


_EFFECT_KEYS = ("domain", "operation", "object_type")


def _effect_identity(value: Any) -> tuple[str, str, str]:
    source = value if isinstance(value, dict) else {}
    return tuple(str(source.get(key) or "").strip().casefold() for key in _EFFECT_KEYS)  # type: ignore[return-value]


def _effect_key(value: tuple[str, str, str]) -> str:
    domain, operation, object_type = value
    return f"{domain}.{operation}:{object_type}" if domain and operation and object_type else ""


def _user_turns(case: dict[str, Any]) -> list[str]:
    return [
        str(row.get("text") or "")
        for row in list(case.get("turns") or [])
        if isinstance(row, dict) and row.get("role") == "user" and str(row.get("text") or "")
    ]


def _match_oracle(
    *,
    case_id: str,
    oracle: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    registered_effect_identities: set[str] | None = None,
) -> None:
    registered_effect_identities = registered_effect_identities or set()
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
        required = bool(expected.get("required", True))
        expected_effect = _effect_identity(expected.get("requested_effect"))
        match_mode = str(expected.get("requested_effect_match") or "exact").strip().casefold()
        if match_mode not in {"exact", "unregistered_open"}:
            raise RuntimeError(f"{case_id}: unsupported requested_effect_match={match_mode!r}")
        if not all(expected_effect):
            raise RuntimeError(
                f"{case_id}: oracle goal {expected.get('oracle_id')!r} lacks requested_effect identity"
            )

        def candidate_matches(row: dict[str, Any], *, fuzzy_span: bool) -> bool:
            span_ok = (
                _span_matches_oracle(expected=evidence, actual=row.get("evidence_span"))
                if fuzzy_span
                else str(row.get("evidence_span") or "") == evidence
            )
            candidate_effect = _effect_identity(row.get("requested_effect"))
            if match_mode == "unregistered_open":
                effect_ok = bool(all(candidate_effect)) and _effect_key(candidate_effect) not in registered_effect_identities
            else:
                effect_ok = _effect_identity(row.get("requested_effect")) == expected_effect
            return (
                span_ok
                and bool(row.get("required", True)) == required
                and effect_ok
            )

        exact_matches = [row for row in unmatched if candidate_matches(row, fuzzy_span=False)]
        matches = exact_matches or [row for row in unmatched if candidate_matches(row, fuzzy_span=True)]
        if len(matches) != 1:
            candidates = [
                {
                    "goal_id": str(row.get("goal_id") or ""),
                    "evidence_span": str(row.get("evidence_span") or ""),
                    "goal_type": str(row.get("goal_type") or ""),
                    "requested_effect": {
                        key: str((row.get("requested_effect") or {}).get(key) or "")
                        for key in _EFFECT_KEYS
                    } if isinstance(row.get("requested_effect"), dict) else {},
                }
                for row in unmatched
            ]
            raise RuntimeError(
                f"{case_id}: no unique model goal matches oracle span={evidence!r}, "
                f"requested_effect={expected_effect!r}, match_mode={match_mode!r}, candidates={candidates!r}"
            )
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
        effect_index = capability_effect_index(get_runtime_registry().capabilities)
        effect_vocabulary_json = json.dumps(
            effect_index,
            ensure_ascii=False,
            sort_keys=True,
        )
        registered_effect_identities = {
            str(row.get("requested_effect_identity") or "").strip().casefold()
            for row in list(effect_index.get("effects") or [])
            if isinstance(row, dict) and str(row.get("requested_effect_identity") or "").strip()
        }
        bound = model.bind_tools(planning_schemas()) if hasattr(model, "bind_tools") else model
        evidence: list[dict[str, Any]] = []
        system = SystemMessage(content=(
            "只执行目标声明：调用 declare_turn_goals，完整保留用户的每一个目标、条件和依赖。"
            "Goal 只表示用户可独立判断完成与否的业务效果；筛选、选目标、输入、前置校验、政策读取、Draft 和展示都只是实现步骤，不能单独提升为 Goal。"
            "requested_effect 必须完整填写 domain、operation、object_type；若下面登记词汇中存在与用户业务效果精确对应的身份，必须逐字段使用；"
            "若不存在精确对应，保留开放业务效果，禁止用 query/action 等泛化类别或相近能力迁就。"
            f"当前部署登记的业务效果身份及模块语义边界（只帮助模型选择结构化 identity；Runtime 仍 exact-match，不是关键词分类器）：{effect_vocabulary_json}。"
            "能力词汇中没有精确身份的分支也必须保留成独立 Goal；requested_effect 要写用户实际请求的开放业务效果，不能写 unsupported_request、能力缺失或系统不支持来替代用户语义。"
            "不能把不支持分支吞掉，也不能用相似能力代替。evidence_span 必须来自用户原话。"
            "多目标时，每个 Goal 的 evidence_span 必须只覆盖该 Goal 的局部连续原文，不能把整句或兄弟 Goal 的文字重复给多个 Goal。"
            "同一当前轮中后续目标依赖前一目标时只用 depends_on；reference_expression 只用于已经在更早轮次向客户展示的历史结果，"
            "不能引用本轮尚未执行目标的未来结果。"
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
                _match_oracle(
                    case_id=case["id"],
                    oracle=oracle,
                    goals=goals,
                    registered_effect_identities=registered_effect_identities,
                )
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
        print(json.dumps({
            "status": "BLOCKED_BY_ENVIRONMENT" if environment_blocked else "FAIL",
            "error_type": exc.__class__.__name__,
            "error_category": category,
            "reason": "configured_model_environment_unavailable" if environment_blocked else "semantic_prototype_certification_failed",
            "error": str(exc),
        }, ensure_ascii=False))
        return 78 if environment_blocked else 1


if __name__ == "__main__":
    raise SystemExit(main())
