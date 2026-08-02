from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


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
            "ctx-multi-intent-12: goal count mismatch, expected 2, got 1; "
            "raw-model-secret-must-not-escape"
        )
    )

    assert result == {
        "error_code": "semantic_goal_count_mismatch__ctx-multi-intent-12",
        "failure_code": "goal_count_mismatch",
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
