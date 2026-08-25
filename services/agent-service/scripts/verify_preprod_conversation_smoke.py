#!/usr/bin/env python3
"""Read-only real-model smoke for independent semantic goal prototypes.

The smoke exercises only the planning protocol. It never builds the lifecycle
graph, opens a Draft, or dispatches a BusinessPort. Each real-model
``declare_turn_goals`` call is first validated through the same canonical
semantic contract as the live Runtime, then compared with an independent
canonical-output oracle. A multi-intent turn therefore fails when the model
drops one branch or changes its user-visible semantic output even if a nearby
legacy effect or installed tool could otherwise execute something similar.
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

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage  # noqa: E402

from agent_core.config import get_model, get_model_profile  # noqa: E402
from agent_core.lifecycle.protocol import planning_schemas  # noqa: E402
from agent_core.lifecycle.goal_planning import validate_goal_declaration  # noqa: E402
from agent_core.goal_graph.compiler import compile_frozen_semantic_contract  # noqa: E402
from agent_core.goal_graph.verifier import dataflow_closure  # noqa: E402
from agent_core.lifecycle.dialogue_runtime import (  # noqa: E402
    _semantic_writer_declaration_result_projection,
)
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
from agent_core.runtime.profile import resolve_verifier_mode  # noqa: E402
from agent_core.composition import get_module_registry, get_runtime_registry  # noqa: E402

CATALOG = WORKSPACE / "services/agent-service/tests/context/strong_context_cases/semantic_goal_coverage_suite_v20_4.json"


# Independent certification expectations. These are test-oracle semantics, not
# capability identities: no tool name, capability key or availability signal is
# used to derive them. Exact alternative sets are listed only where the user
# wording legitimately admits more than one canonical semantic decomposition.
_SHIPMENT_OVERVIEW_OUTPUT_SETS: tuple[tuple[str, ...], ...] = (
    ("shipment.current_status",),
    ("shipment.eta",),
    ("shipment.tracking",),
    ("shipment.current_status", "shipment.eta"),
    ("shipment.current_status", "shipment.tracking"),
    ("shipment.eta", "shipment.tracking"),
    ("shipment.current_status", "shipment.eta", "shipment.tracking"),
)

_CANONICAL_OUTPUT_ORACLE: dict[str, dict[str, tuple[tuple[str, ...], ...]]] = {
    "semantic_multi_orders_logistics": {
        "g1": (("order.collection",),),
        "g2": (
            ("shipment.tracking",),
            ("shipment.current_status", "shipment.tracking"),
        ),
    },
    "semantic_multi_refunds_invoices": {
        "g1": (("refund.status",),),
        "g2": (("invoice.status",),),
    },
    "semantic_query_then_refund_consult": {
        "g1": (("order.collection",),),
        "g2": (("refund.eligibility",),),
    },
    "semantic_query_then_refund_draft": {
        "g1": (("order.collection",),),
        "g2": (("refund.request",),),
    },
    "semantic_cancel_and_refund_branch": {
        "g1": (("order.cancellation",),),
        "g2": (("refund.eligibility",),),
    },
    "semantic_supported_plus_unsupported": {
        "g1": _SHIPMENT_OVERVIEW_OUTPUT_SETS,
        "g2": (("courier.contact.phone",),),
    },
    "semantic_unsupported_courier_phone": {
        "g1": (("courier.contact.phone",),),
    },
    # The installed ecommerce vocabulary intentionally has no refund ETA
    # meaning. The reserved ``open`` output preserves the user's actual request
    # rather than inventing a nearby canonical ID.
    "semantic_refund_arrival_query": {"g1": (("open",),)},
    "semantic_delete_record_not_cancel": {"g1": (("open",),)},
    "semantic_refund_consult_no_draft": {"g1": (("refund.eligibility",),)},
    "semantic_multi_target_cancel_boundary": {"g1": (("order.cancellation",),)},
    "semantic_conflicting_actions_clarify": {"g1": (("open",),)},
}


def _compact_span(value: Any) -> str:
    """Normalize presentation-only differences without rewriting semantics."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s,，。.!！?？;；:：、]+", "", normalized)


def _span_matches_oracle(*, expected: Any, actual: Any) -> bool:
    """Accept a literal model span that adds surrounding user wording.

    Goal spans are independently checked by the production validator to be
    literal substrings of the current user turn. The oracle therefore owns the
    semantic core, while the model may legitimately include an adjacent verb or
    conjunction. Containment is deliberately one-dimensional; fuzzy similarity
    and token overlap remain forbidden.
    """

    oracle_span = _compact_span(expected)
    model_span = _compact_span(actual)
    return bool(oracle_span and model_span and (
        oracle_span == model_span
        or oracle_span in model_span
        or model_span in oracle_span
    ))


def _semantic_vocabulary_snapshot() -> dict[str, Any]:
    """Return only module-owned domain semantics, never capability availability."""

    snapshot = get_module_registry().semantic_vocabulary_snapshot()
    if snapshot.get("availability_exposed") is not False:
        raise RuntimeError("semantic planning vocabulary must not expose capability availability")
    if snapshot.get("tool_names_exposed") is not False:
        raise RuntimeError("semantic planning vocabulary must not expose tool names")
    return snapshot


def _semantic_output_ids() -> tuple[str, ...]:
    return tuple(get_module_registry().semantic_output_ids())


def _requested_output_identity(row: dict[str, Any]) -> tuple[str, ...]:
    effect = row.get("requested_effect") if isinstance(row.get("requested_effect"), dict) else {}
    raw_outputs = effect.get("requested_outputs") if isinstance(effect, dict) else []
    output_ids: list[str] = []
    for item in list(raw_outputs or []):
        output_id = (
            str(item.get("output_id") or "").strip().casefold()
            if isinstance(item, dict)
            else str(item or "").strip().casefold()
        )
        if output_id:
            output_ids.append(output_id)
    return tuple(sorted(dict.fromkeys(output_ids)))


def _oracle_output_sets(*, case_id: str, expected: dict[str, Any]) -> tuple[tuple[str, ...], ...]:
    """Resolve independent canonical expectations without consulting capabilities."""

    explicit = expected.get("accepted_output_sets")
    if isinstance(explicit, list):
        values = tuple(
            tuple(sorted(dict.fromkeys(
                str(value or "").strip().casefold()
                for value in row
                if str(value or "").strip()
            )))
            for row in explicit
            if isinstance(row, list)
        )
    else:
        values = _CANONICAL_OUTPUT_ORACLE.get(case_id, {}).get(
            str(expected.get("oracle_id") or ""),
            (),
        )
    if not values:
        raise RuntimeError(
            f"{case_id}: canonical semantic oracle missing for {expected.get('oracle_id')!r}"
        )
    if any(not row for row in values):
        raise RuntimeError(f"{case_id}: canonical oracle output set must not be empty")

    registered = {value.casefold() for value in _semantic_output_ids()}
    unknown = sorted({
        output_id
        for row in values
        for output_id in row
        if output_id != "open" and output_id not in registered
    })
    if unknown:
        raise RuntimeError(
            f"{case_id}: canonical oracle references unregistered outputs: {unknown}"
        )
    return values


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
    declared: dict[str, Any] | None = None,
) -> None:
    """Match only canonical requested outputs; legacy triplets are non-authoritative."""

    # Kept only as a call-signature compatibility parameter for focused tests
    # from older repair attempts. It is deliberately ignored as semantic input.
    del registered_effect_identities

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
        accepted_outputs = _oracle_output_sets(case_id=case_id, expected=expected)

        def candidate_matches(row: dict[str, Any], *, containment: bool) -> bool:
            span_ok = (
                _span_matches_oracle(expected=evidence, actual=row.get("evidence_span"))
                if containment
                else str(row.get("evidence_span") or "") == evidence
            )
            return (
                span_ok
                and bool(row.get("required", True)) == required
                and _requested_output_identity(row) in accepted_outputs
            )

        exact_matches = [row for row in unmatched if candidate_matches(row, containment=False)]
        matches = exact_matches or [
            row for row in unmatched if candidate_matches(row, containment=True)
        ]
        if len(matches) != 1:
            candidates = [
                {
                    "goal_id": str(row.get("goal_id") or ""),
                    "evidence_span": str(row.get("evidence_span") or ""),
                    "requested_outputs": list(_requested_output_identity(row)),
                }
                for row in unmatched
            ]
            raise RuntimeError(
                f"{case_id}: no unique model goal matches oracle span={evidence!r}, "
                f"accepted_outputs={accepted_outputs!r}, candidates={candidates!r}"
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
    compiled_dependencies: dict[str, list[str]] | None = None
    if isinstance(declared, dict):
        frozen = declared.get("_frozen_semantic_contract")
        if not isinstance(frozen, dict):
            raise RuntimeError(f"{case_id}: production declaration did not retain FrozenSemanticContract")
        graph = compile_frozen_semantic_contract(
            frozen,
            scope={"tenant_id": "preprod", "user_id": "preprod", "thread_id": case_id},
        )
        closure = dataflow_closure(graph, frozen_contract=frozen)
        if not closure.get("ok"):
            raise RuntimeError(f"{case_id}: compiled dependency graph is not closed: {closure!r}")
        compiled_dependencies = dict(closure.get("derived_dependencies") or {})
    for expected in oracle:
        expected_dependencies = {
            oracle_to_goal.get(str(value), "<unmatched>")
            for value in expected.get("depends_on") or []
        }
        goal = goals_by_id[oracle_to_goal[str(expected["oracle_id"])]]
        actual_dependencies = {
            str(value)
            for value in (
                compiled_dependencies.get(str(goal.get("goal_id") or ""), [])
                if compiled_dependencies is not None
                else goal.get("depends_on") or []
            )
        }
        if actual_dependencies != expected_dependencies:
            raise RuntimeError(
                f"{case_id}: goal dependency mismatch for oracle {expected['oracle_id']}; "
                f"expected_dependencies={sorted(expected_dependencies)!r}; "
                f"actual_dependencies={sorted(actual_dependencies)!r}"
            )


def _production_goal_declaration_evaluation(
    *,
    user_text: str,
    goals: list[dict[str, Any]],
    inventory_authority: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Evaluate through the same strict canonical contract as production Runtime."""

    state: dict[str, Any] = {"current_user_input": user_text}
    if isinstance(inventory_authority, dict):
        state["current_turn_plan"] = {
            "goal_granularity_inventory_authority": dict(inventory_authority),
        }
    return validate_goal_declaration(
        state=state,
        args={"goals": goals},
        capability_registry=get_runtime_registry().capabilities,
        require_canonical_output_identity=True,
    )


def _sanitized_goal_rejection_diagnostic(result: dict[str, Any] | None) -> dict[str, Any]:
    payload = result if isinstance(result, dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    diagnostic: dict[str, Any] = {"code": str(payload.get("code") or "")}
    alignment = data.get("alignment_proof") if isinstance(data.get("alignment_proof"), dict) else None
    if alignment is not None:
        alignment_details = alignment.get("details") if isinstance(alignment.get("details"), dict) else {}
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
            "dependency_authority": str(alignment_details.get("dependency_authority") or ""),
            "dependency_proof_complete": bool(alignment_details.get("dependency_proof_complete")),
            "dependency_graph_match": alignment_details.get("dependency_graph_match"),
            "dependency_edges": [
                {
                    "dependent_goal_id": str(row.get("dependent_goal_id") or ""),
                    "requires_result_of_goal_id": str(row.get("requires_result_of_goal_id") or ""),
                    "basis_kind": str(row.get("basis_kind") or ""),
                    "basis_span": str(row.get("basis_span") or ""),
                }
                for row in list(alignment_details.get("dependency_edges") or [])
                if isinstance(row, dict)
            ][:8],
        }
    granularity = data.get("granularity_proof") if isinstance(data.get("granularity_proof"), dict) else None
    if granularity is not None:
        details = granularity.get("details") if isinstance(granularity.get("details"), dict) else {}
        diagnostic["granularity"] = {
            "verdict": str(granularity.get("verdict") or ""),
            "reason_code": str(granularity.get("reason_code") or ""),
            "inventory_outcome_count": details.get("inventory_outcome_count"),
            "declared_goal_count": details.get("declared_goal_count"),
            "matched_outcome_count": details.get("matched_outcome_count"),
            "outcome_spans": [str(value) for value in list(details.get("outcome_spans") or []) if str(value)][:8],
            "authority_scope": str(details.get("authority_scope") or ""),
            "dependency_authority": str(details.get("dependency_authority") or ""),
            "inventory_authority_reused": bool(details.get("inventory_authority_reused")),
            "blind_self_audit_attempted": bool(details.get("blind_self_audit_attempted")),
        }
    feedback = data.get("independent_verifier_feedback") if isinstance(data.get("independent_verifier_feedback"), dict) else None
    if feedback is not None:
        diagnostic["independent_verifier_feedback"] = {
            "authority": str(feedback.get("authority") or ""),
            "uncovered_outcome_spans": [str(value) for value in list(feedback.get("uncovered_outcome_spans") or []) if str(value)][:8],
            "dependency_edges": [
                {
                    key: str(row.get(key) or "")
                    for key in (
                        "dependent_goal_id",
                        "requires_result_of_goal_id",
                        "basis_kind",
                        "basis_span",
                        "dependent_span",
                        "requires_result_of_span",
                    )
                    if str(row.get(key) or "")
                }
                for row in list(feedback.get("dependency_edges") or [])
                if isinstance(row, dict)
            ][:8],
        }
    return diagnostic


def _semantic_writer_rejection_tool_message(
    *,
    tool_call_id: str,
    result: dict[str, Any],
) -> ToolMessage:
    """Project a rejected declaration through the live Runtime writer boundary.

    Certification is allowed to validate with the same complete audit proof as
    production, but the next Semantic Writer call must see exactly the same
    candidate-blind, violation-only projection as the live dialogue Runtime.
    Keeping this adapter in the harness makes the protocol seam directly
    regression-testable without duplicating projection semantics.
    """

    projected = _semantic_writer_declaration_result_projection(result)
    return ToolMessage(
        tool_call_id=tool_call_id,
        name="declare_turn_goals",
        content=json.dumps(projected, ensure_ascii=False, default=str),
    )


class _ProductionGoalDeclarationRejected(RuntimeError):
    def __init__(self, *, case_id: str, result: dict[str, Any]):
        self.result = result
        errors = (result.get("data") or {}).get("errors") or [result.get("code")]
        super().__init__(f"{case_id}: production goal declaration rejected model output: {errors}")


def _validate_with_production_goal_contract(
    *,
    case_id: str,
    user_text: str,
    goals: list[dict[str, Any]],
    inventory_authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result, declared = _production_goal_declaration_evaluation(
        user_text=user_text,
        goals=goals,
        inventory_authority=inventory_authority,
    )
    if not result.get("ok") or declared is None:
        raise _ProductionGoalDeclarationRejected(case_id=case_id, result=result)
    return declared


_LITERAL_GROUNDING_ERROR_PREFIX = "evidence_not_in_current_turn:"
_SEMANTIC_VERIFIER_DATA_KEYS = (
    "alignment_proof",
    "granularity_proof",
    "independent_verifier_feedback",
)


def _is_pure_literal_grounding_rejection(result: dict[str, Any] | None) -> bool:
    """Admit a final declaration-only retry only for pre-verifier literal failures."""

    if not isinstance(result, dict) or str(result.get("code") or "") != "GOAL_DECLARATION_INVALID":
        return False
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if any(key in data for key in _SEMANTIC_VERIFIER_DATA_KEYS):
        return False
    raw_errors = data.get("errors")
    if not isinstance(raw_errors, list):
        return False
    errors = [str(value) for value in raw_errors if str(value)]
    return bool(errors) and all(
        error.startswith(_LITERAL_GROUNDING_ERROR_PREFIX)
        for error in errors
    )


def _literal_grounding_retry_result(
    *,
    user_text: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    errors = [
        str(value)
        for value in list(data.get("errors") or [])
        if str(value)
    ]
    return {
        "ok": False,
        "code": "GOAL_DECLARATION_LITERAL_GROUNDING_RETRY",
        "message": "Final declaration-only retry: repair literal evidence copying only.",
        "data": {
            "errors": errors,
            "current_user_input": user_text,
            "repair_contract": {
                "authority": "current_user_input_only",
                "required_action": "redeclaration",
                "retry_kind": "literal_grounding_only",
                "rules": [
                    "Copy every evidence_span as exact contiguous characters from current_user_input.",
                    "Preserve every semantic branch and dependency already intended in the immediately preceding declaration.",
                    "Do not paraphrase evidence_span or change business meaning.",
                ],
                "forbidden": [
                    "oracle answers",
                    "expected tool or capability identity",
                    "normalized business values",
                    "fuzzy or keyword hints",
                ],
            },
        },
    }


def _declare_with_bounded_production_repair(
    *,
    case_id: str,
    user_text: str,
    bound: Any,
    system: SystemMessage,
    identity: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], int]:
    """Return the first declaration that production validators can freeze.

    The normal semantic declaration repair budget remains two attempts. One
    final declaration-only retry is available only when both normal attempts
    fail before semantic verification on pure literal ``evidence_span``
    grounding. That retry can only copy exact user text while preserving the
    model's own immediately preceding semantic branches; it receives no oracle,
    capability answer, normalized business value, fuzzy hint, or Runtime rewrite.
    """

    messages: list[Any] = [system, HumanMessage(content=user_text)]
    last_result: dict[str, Any] | None = None
    inventory_authority: dict[str, Any] | None = None
    literal_grounding_only_history = True
    last_response: Any | None = None
    last_call: dict[str, Any] | None = None
    for attempt in range(1, 3):
        response, trace = invoke_model(
            purpose=f"preprod_semantic_goal:{case_id}:attempt{attempt}",
            model=bound,
            payload=messages,
        )
        attestation = attest_real_model_metadata(response=response, identity=identity)
        candidates = tool_calls(response)
        if len(candidates) != 1 or str(candidates[0].get("name") or "") != "declare_turn_goals":
            raise RuntimeError(f"{case_id}: model did not emit exactly one declare_turn_goals call")
        call = candidates[0]
        last_response = response
        last_call = call
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        goals = [row for row in list(args.get("goals") or []) if isinstance(row, dict)]
        # Repair invariant: 不能删除系统没有精确能力的分支；the exact Runtime result below supplies the deterministic reason.
        try:
            declared = _validate_with_production_goal_contract(
                case_id=case_id,
                user_text=user_text,
                goals=goals,
                inventory_authority=inventory_authority,
            )
            return goals, declared, {"trace": trace, "attestation": attestation}, attempt
        except RuntimeError as exc:
            if not isinstance(exc, _ProductionGoalDeclarationRejected):
                raise
            result = exc.result
            last_result = result
            literal_grounding_only_history = (
                literal_grounding_only_history
                and _is_pure_literal_grounding_rejection(result)
            )
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            granularity = data.get("granularity_proof") if isinstance(data.get("granularity_proof"), dict) else {}
            details = granularity.get("details") if isinstance(granularity.get("details"), dict) else {}
            candidate_authority = details.get("inventory_authority")
            if isinstance(candidate_authority, dict):
                inventory_authority = dict(candidate_authority)
        if attempt >= 2:
            break
        tool_call_id = str(call.get("id") or f"{case_id}:declare:{attempt}")
        # Live dialogue Runtime projects rejected declaration results before the
        # next Semantic Writer call. Certification must cross the same canonical
        # provider-facing boundary rather than feeding the complete audit proof
        # back to the model. This preserves verifier evidence for audit while
        # exposing only current input plus field-specific violation evidence.
        messages = [
            system,
            HumanMessage(content=user_text),
            response,
            _semantic_writer_rejection_tool_message(
                tool_call_id=tool_call_id,
                result=result,
            ),
        ]

    if (
        literal_grounding_only_history
        and _is_pure_literal_grounding_rejection(last_result)
        and last_response is not None
        and last_call is not None
    ):
        # Both normal attempts failed before alignment/granularity verification,
        # so this extra declaration call does not create a new verifier budget.
        # The final candidate may enter the existing verifier envelope once; no
        # further declaration repair is available after this attempt.
        literal_retry = _literal_grounding_retry_result(
            user_text=user_text,
            result=last_result,
        )
        tool_call_id = str(last_call.get("id") or f"{case_id}:declare:2")
        messages = [
            system,
            HumanMessage(content=user_text),
            last_response,
            ToolMessage(
                tool_call_id=tool_call_id,
                name="declare_turn_goals",
                content=json.dumps(literal_retry, ensure_ascii=False, default=str),
            ),
        ]
        response, trace = invoke_model(
            purpose=f"preprod_semantic_goal:{case_id}:attempt3",
            model=bound,
            payload=messages,
        )
        attestation = attest_real_model_metadata(response=response, identity=identity)
        candidates = tool_calls(response)
        if len(candidates) != 1 or str(candidates[0].get("name") or "") != "declare_turn_goals":
            raise RuntimeError(f"{case_id}: model did not emit exactly one declare_turn_goals call")
        call = candidates[0]
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        goals = [row for row in list(args.get("goals") or []) if isinstance(row, dict)]
        try:
            declared = _validate_with_production_goal_contract(
                case_id=case_id,
                user_text=user_text,
                goals=goals,
                inventory_authority=inventory_authority,
            )
            return goals, declared, {"trace": trace, "attestation": attestation}, 3
        except RuntimeError as exc:
            if not isinstance(exc, _ProductionGoalDeclarationRejected):
                raise
            last_result = exc.result

    errors = ((last_result or {}).get("data") or {}).get("errors") or [(last_result or {}).get("code")]
    diagnostic = _sanitized_goal_rejection_diagnostic(last_result)
    raise RuntimeError(
        f"{case_id}: bounded production declaration repair exhausted: "
        f"{case_id}: production goal declaration rejected model output: {errors}; "
        f"verifier_diagnostic={json.dumps(diagnostic, ensure_ascii=False, sort_keys=True)}"
    )


def _identity_failure_reason(exc: RealModelCertificationError) -> str:
    if exc.environment_blocked:
        return "real_model_environment_unavailable"
    if exc.phase == "response":
        return "real_model_response_attestation_invalid"
    return "real_model_identity_invalid"


def _semantic_verifier_authority() -> dict[str, str]:
    modes = {
        name: resolve_verifier_mode(name)
        for name in (
            "GOAL_ALIGNMENT_VERIFIER_MODE",
            "GOAL_GRANULARITY_VERIFIER_MODE",
        )
    }
    invalid = {name: value for name, value in modes.items() if value != "model"}
    if invalid:
        raise RuntimeError(
            "semantic certification requires protected model verifier authority: "
            + json.dumps(invalid, sort_keys=True)
        )
    return modes


def main() -> int:
    try:
        identity = resolve_real_model_identity()
        verifier_authority = _semantic_verifier_authority()
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
        semantic_vocabulary = _semantic_vocabulary_snapshot()
        semantic_vocabulary_json = json.dumps(
            semantic_vocabulary,
            ensure_ascii=False,
            sort_keys=True,
        )
        semantic_output_ids = _semantic_output_ids()
        bound = (
            model.bind_tools(planning_schemas(semantic_output_ids=semantic_output_ids))
            if hasattr(model, "bind_tools")
            else model
        )
        evidence: list[dict[str, Any]] = []
        system = SystemMessage(content=(
            "只执行目标声明：调用 declare_turn_goals，完整保留用户的每一个目标、条件和输入来源。"
            "Goal 只表示用户可独立判断完成与否的业务效果；筛选、选目标、输入、前置校验、政策读取、Draft 和展示都只是实现步骤，不能单独提升为 Goal。"
            "requested_effect.requested_outputs 是新轮语义身份的唯一权威；每个 output_id 必须从下面的 capability-independent semantic vocabulary 精确选择。"
            "domain、operation、object_type 仍按用户开放业务语义填写，但只是非权威兼容元数据，不能覆盖 requested_outputs。"
            "若词汇中不存在用户实际请求的概念，output_id 只能使用保留值 open，并在 open_description 中保留用户真正要的结果；禁止用 query/action 等泛化类别或相近 semantic output 迁就。"
            f"当前部署登记的 canonical semantic outputs（不包含能力可用性和工具身份）：{semantic_vocabulary_json}。"
            "能力词汇中没有精确身份的分支也必须保留成独立 Goal；不能把不支持分支吞掉，也不能用相似能力代替。"
            "evidence_span 必须来自用户原话；requested_outputs[].evidence_span 也必须是该 Goal 原话中的直接连续证据。"
            "多目标时，每个 Goal 的 evidence_span 必须只覆盖该 Goal 的局部连续原文，不能把整句或兄弟 Goal 的文字重复给多个 Goal。局部 evidence_span 只证明该 Goal 的业务效果；若后续独立 Goal 以零指代继承同句前文已经明示的目标身份，不要求把前文目标词复制进后续 evidence_span，也不得把对象/成员身份伪装成 target_candidate.scope_constraints。只有真正缩小目标/结果人口的筛选、状态、阈值或比较才属于 scope_constraints；完整用户原话中的共享目标身份仍可直接作为同轮语义上下文。"
            "禁止输出 depends_on。只有后一 Goal 的目标或值输入明确消费前一 Goal 尚未产生的输出时，才声明 input_bindings，source.kind=current_goal_output，并精确填写 producer_goal_id、output_id、relation_kind、expected_cardinality 与字面 evidence_span；Runtime 从它确定性编译依赖边。前文已明示对象而后文真正省略重复对象（零指代）只是共享 current_text 范围，不产生依赖；不能因为再/然后等顺序表达、共享主题或执行时需要解析稳定 ID 而制造 current_goal_output。显式指代前一 Goal 本轮结果才是 result_reference。条件若引用前一 Goal 输出，只写 Condition AST 的 goal_output 操作数，不重复添加 binding。reference_expression 与 visible_result_ref 只用于已经在更早轮次向客户展示的历史结果，"
            "不能引用本轮尚未执行目标的未来结果。"
        ))
        # The normal declaration budget remains two attempts. Each accepted
        # declaration is checked by independent semantic auditors, but neither
        # owns or repairs the dependency graph; deterministic compilation of the
        # accepted input bindings is the only dependency authority. The optional third call is
        # declaration-only and is reachable only after two pure literal-grounding
        # failures, which return before either verifier runs. Its worst case is
        # therefore 2 declaration failures + (1 declaration + 2 alignment +
        # 2 granularity) = 7 calls for that case, below the normal two-attempt
        # worst case of 2 * (1 + 2 + 2) = 10. The governed 120-call envelope and
        # each verifier's existing per-declaration cap remain unchanged.
        with model_call_scope(max_calls=120, scope="preprod_semantic_goal_prototypes") as calls:
            for case in cases:
                turn = case["execution_contract"]["turn_contracts"][0]
                goals, declared, declaration_evidence, declaration_attempts = _declare_with_bounded_production_repair(
                    case_id=str(case["id"]),
                    user_text=str(turn["user_text"]),
                    bound=bound,
                    system=system,
                    identity=identity,
                )
                oracle = [row for row in list(turn.get("goal_oracle") or []) if isinstance(row, dict)]
                _match_oracle(
                    case_id=case["id"],
                    oracle=oracle,
                    goals=goals,
                    declared=declared,
                )
                trace = declaration_evidence["trace"]
                attestation = declaration_evidence["attestation"]
                evidence.append({
                    "case_id": case["id"],
                    "goal_count": len(goals),
                    "declaration_attempts": declaration_attempts,
                    "goal_types": [str(row.get("goal_type") or "") for row in goals],
                    "canonical_requested_outputs": [
                        list(_requested_output_identity(row))
                        for row in goals
                    ],
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
            "verifier_authority": verifier_authority,
            "semantic_vocabulary": {
                "authority": semantic_vocabulary.get("authority"),
                "availability_exposed": semantic_vocabulary.get("availability_exposed"),
                "tool_names_exposed": semantic_vocabulary.get("tool_names_exposed"),
                "output_count": len(semantic_output_ids),
            },
            "calls": calls.summary(),
            "cases": evidence,
            "guarantee": (
                "schema-compliant real-model canonical goal declaration, production validation, and dependency semantics; "
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
