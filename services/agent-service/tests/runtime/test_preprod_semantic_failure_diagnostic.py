from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


AGENT_ROOT = Path(__file__).resolve().parents[2]
for path in (AGENT_ROOT, AGENT_ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load_module():
    script = AGENT_ROOT / "scripts" / "verify_preprod_conversation_smoke.py"
    spec = importlib.util.spec_from_file_location("preprod_semantic_failure_diagnostic_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_safe_semantic_failure_exposes_case_and_failure_code_without_model_text() -> None:
    module = _load_module()

    result = module._safe_semantic_failure(
        RuntimeError(
            "ctx-multi-intent-12: required effect evidence not covered: ['g2']; "
            "raw-model-secret-must-not-escape"
        )
    )

    assert result == {
        "error_code": "semantic_required_effect_evidence_missing__ctx-multi-intent-12",
        "failure_code": "required_effect_evidence_missing",
        "failed_case_id": "ctx-multi-intent-12",
    }
    assert "raw-model-secret-must-not-escape" not in str(result)


def test_safe_semantic_failure_classifies_tool_call_shape_without_raw_payload() -> None:
    module = _load_module()

    result = module._safe_semantic_failure(
        RuntimeError(
            "ctx-tool-shape: model did not emit exactly one declare_turn_goals call; "
            "raw-tool-payload-must-not-escape"
        )
    )

    assert result["error_code"] == "semantic_declare_turn_goals_call_invalid__ctx-tool-shape"
    assert result["failure_code"] == "declare_turn_goals_call_invalid"
    assert result["failed_case_id"] == "ctx-tool-shape"
    assert "raw-tool-payload-must-not-escape" not in str(result)


def test_effect_coverage_does_not_promote_legacy_goal_type_or_dependency_shape_to_authority() -> None:
    module = _load_module()
    oracle = [
        {
            "oracle_id": "g1",
            "goal_type": "query",
            "evidence_span": "查一下键盘订单",
            "required": True,
            "depends_on": [],
        },
        {
            "oracle_id": "g2",
            "goal_type": "consult",
            "evidence_span": "它能不能退款",
            "required": True,
            "depends_on": ["g1"],
        },
    ]
    goals = [
        {
            "goal_id": "model-order",
            "goal_type": "query",
            "evidence_span": "查一下键盘订单",
            "required": True,
            "depends_on": [],
            "requested_effect": {"operation": "list"},
        },
        {
            "goal_id": "model-refund-eligibility",
            # Compatibility metadata and dependency shape may differ; the
            # production verifier judges the requested user-visible effects.
            "goal_type": "query",
            "evidence_span": "再看看它能不能退款",
            "required": True,
            "depends_on": [],
            "requested_effect": {"operation": "assess_eligibility"},
        },
    ]

    module._assert_effect_evidence_coverage(
        case_id="semantic_query_then_refund_consult",
        oracle=oracle,
        goals=goals,
    )


def test_effect_coverage_still_rejects_missing_literal_branch() -> None:
    module = _load_module()
    oracle = [
        {
            "oracle_id": "g1",
            "evidence_span": "查一下键盘订单",
            "required": True,
            "depends_on": [],
        },
        {
            "oracle_id": "g2",
            "evidence_span": "它能不能退款",
            "required": True,
            "depends_on": ["g1"],
        },
    ]
    goals = [
        {
            "goal_id": "model-order",
            "evidence_span": "查一下键盘订单",
            "required": True,
            "depends_on": [],
        },
        {
            "goal_id": "model-unrelated",
            "evidence_span": "查一下键盘订单",
            "required": True,
            "depends_on": ["model-order"],
        },
    ]

    with pytest.raises(RuntimeError, match="required effect evidence not covered"):
        module._assert_effect_evidence_coverage(
            case_id="semantic_query_then_refund_consult",
            oracle=oracle,
            goals=goals,
        )
