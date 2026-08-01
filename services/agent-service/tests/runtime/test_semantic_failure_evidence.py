from __future__ import annotations

import importlib.util
import io
import json
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "services" / "agent-service" / "src"
SCRIPTS = ROOT / "scripts"
for item in (SRC, SCRIPTS):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from agent_core.model_calls.real_model_certification_bundle import run_certification_bundle


def _load_semantic_script():
    path = ROOT / "services" / "agent-service" / "scripts" / "verify_preprod_conversation_smoke.py"
    spec = importlib.util.spec_from_file_location("semantic_failure_evidence_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _catalog() -> dict:
    cases = []
    for index in range(12):
        text = f"查询订单 {index}"
        cases.append({
            "id": f"semantic-case-{index}",
            "turns": [{"role": "user", "text": text}],
            "execution_contract": {
                "preproduction_risk_prototype": True,
                "turn_contracts": [{
                    "user_text": text,
                    "goal_oracle": [{
                        "oracle_id": "goal-1",
                        "evidence_span": text,
                        "goal_type": "query",
                        "required": True,
                        "depends_on": [],
                        "required_tools": ["list_orders"],
                    }],
                }],
            },
        })
    return {"cases": cases}


def _official_key() -> str:
    return "sk-live-" + ("c" * 48)


def test_semantic_script_emits_bounded_case_and_stage_without_model_text(monkeypatch, tmp_path: Path) -> None:
    script = _load_semantic_script()
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(_catalog(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(script, "CATALOG", catalog)
    monkeypatch.setenv("OPENAI_API_KEY", _official_key())
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("REAL_MODEL_CERTIFICATION_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)

    class Model:
        def bind_tools(self, _schemas):
            return self

    @contextmanager
    def scope(**_kwargs):
        yield SimpleNamespace(summary=lambda: {"calls": 1})

    response = SimpleNamespace(
        content="sensitive-model-text-must-not-propagate",
        id="msg-semantic-failure",
        response_metadata={
            "model_name": "gpt-4o-mini-2024-07-18",
            "finish_reason": "stop",
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )
    monkeypatch.setattr(script, "get_model", lambda: Model())
    monkeypatch.setattr(script, "model_call_scope", scope)
    monkeypatch.setattr(script, "invoke_model", lambda **_kwargs: (response, {"purpose": "test"}))
    monkeypatch.setattr(script, "tool_calls", lambda _response: [])

    output = io.StringIO()
    with redirect_stdout(output):
        return_code = script.main()
    payload = json.loads(output.getvalue().strip().splitlines()[-1])

    assert return_code == 1
    assert payload["status"] == "FAIL"
    assert payload["error_code"] == "semantic_tool_call_shape_invalid"
    assert payload["case_id"] == "semantic-case-0"
    assert payload["failure_stage"] == "tool_call_shape"
    assert "error" not in payload
    assert "sensitive-model-text" not in output.getvalue()
    assert _official_key() not in output.getvalue()


def test_inner_bundle_preserves_only_safe_semantic_failure_fields() -> None:
    def runner(**kwargs):
        component = kwargs["component"]
        if component == "smoke":
            return {"status": "PASS"}
        assert component == "semantic"
        return {
            "status": "FAIL",
            "reason": "semantic_prototype_certification_failed",
            "error_code": "semantic_goal_count_mismatch",
            "error_type": "RuntimeError",
            "error_category": "model_output_invalid",
            "case_id": "semantic-case-3",
            "failure_stage": "oracle_match",
            "error": "raw model output must not propagate",
            "api_key": "must not propagate",
        }

    result = run_certification_bundle(
        workspace_root=ROOT,
        env={
            "OPENAI_API_KEY": _official_key(),
            "OPENAI_MODEL": "gpt-4o-mini",
            "REAL_MODEL_CERTIFICATION_PROVIDER": "openai",
        },
        component_runner=runner,
    )

    assert result["status"] == "FAIL"
    assert result["failed_component"] == "semantic"
    assert result["error_code"] == "semantic_goal_count_mismatch"
    assert result["component_failure"] == {
        "component": "semantic",
        "status": "FAIL",
        "reason": "semantic_prototype_certification_failed",
        "error_code": "semantic_goal_count_mismatch",
        "error_type": "RuntimeError",
        "error_category": "model_output_invalid",
        "case_id": "semantic-case-3",
        "failure_stage": "oracle_match",
    }
    assert "raw model output" not in str(result)
    assert "api_key" not in str(result)
