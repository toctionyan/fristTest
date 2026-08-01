#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


semantic = ROOT / "services/agent-service/scripts/verify_preprod_conversation_smoke.py"
replace_once(
    semantic,
    '''def main() -> int:
    try:
        identity = resolve_real_model_identity()
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
''',
    '''def _semantic_failure_code(*, stage: str, exc: Exception) -> str:
    message = str(exc)
    known = (
        ("goal count mismatch", "semantic_goal_count_mismatch"),
        ("duplicate goal_id", "semantic_duplicate_goal_id"),
        ("no unique model goal matches oracle", "semantic_oracle_goal_mismatch"),
        ("model emitted undeclared extra goals", "semantic_extra_goal"),
        ("goal dependency mismatch", "semantic_dependency_mismatch"),
        ("oracle and model goals must declare stable IDs", "semantic_goal_id_missing"),
        ("production goal declaration rejected model output", "semantic_production_goal_contract_rejected"),
        ("did not emit exactly one declare_turn_goals", "semantic_tool_call_shape_invalid"),
        ("expected exactly 12 semantic prototypes", "semantic_catalog_count_invalid"),
        ("must currently be single-turn", "semantic_catalog_turn_shape_invalid"),
    )
    for marker, code in known:
        if marker in message:
            return code
    normalized_stage = re.sub(r"[^a-z0-9_]+", "_", str(stage or "unknown").casefold()).strip("_")
    return f"semantic_{normalized_stage or 'unknown'}_failed"


def main() -> int:
    current_case_id = ""
    failure_stage = "identity"
    try:
        identity = resolve_real_model_identity()
        failure_stage = "catalog_load"
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
''',
    label="add semantic failure classifier",
)
replace_once(
    semantic,
    '''        model = get_model()
        bound = model.bind_tools(planning_schemas()) if hasattr(model, "bind_tools") else model
''',
    '''        failure_stage = "model_initialize"
        model = get_model()
        bound = model.bind_tools(planning_schemas()) if hasattr(model, "bind_tools") else model
''',
    label="mark model initialization stage",
)
replace_once(
    semantic,
    '''            for case in cases:
                turn = case["execution_contract"]["turn_contracts"][0]
                response, trace = invoke_model(
''',
    '''            for case in cases:
                current_case_id = str(case["id"])
                turn = case["execution_contract"]["turn_contracts"][0]
                failure_stage = "model_invoke"
                response, trace = invoke_model(
''',
    label="mark semantic case and invoke stage",
)
replace_once(
    semantic,
    '''                attestation = attest_real_model_metadata(
                    response=response,
                    identity=identity,
                )
                candidates = tool_calls(response)
''',
    '''                failure_stage = "metadata_attestation"
                attestation = attest_real_model_metadata(
                    response=response,
                    identity=identity,
                )
                failure_stage = "tool_call_shape"
                candidates = tool_calls(response)
''',
    label="mark attestation and tool shape stages",
)
replace_once(
    semantic,
    '''                oracle = [row for row in list(turn.get("goal_oracle") or []) if isinstance(row, dict)]
                _match_oracle(case_id=case["id"], oracle=oracle, goals=goals)
                declared = _validate_with_production_goal_contract(
''',
    '''                oracle = [row for row in list(turn.get("goal_oracle") or []) if isinstance(row, dict)]
                failure_stage = "oracle_match"
                _match_oracle(case_id=case["id"], oracle=oracle, goals=goals)
                failure_stage = "production_goal_contract"
                declared = _validate_with_production_goal_contract(
''',
    label="mark oracle and production contract stages",
)
replace_once(
    semantic,
    '''                evidence.append({
                    "case_id": case["id"],
''',
    '''                failure_stage = "evidence_append"
                evidence.append({
                    "case_id": case["id"],
''',
    label="mark evidence append stage",
)
replace_once(
    semantic,
    '''        print(json.dumps({
            "status": "BLOCKED_BY_ENVIRONMENT" if environment_blocked else "FAIL",
            "error_type": exc.__class__.__name__,
            "error_category": category,
            "reason": "configured_model_environment_unavailable" if environment_blocked else "semantic_prototype_certification_failed",
            "error": str(exc),
        }, ensure_ascii=False))
''',
    '''        print(json.dumps({
            "status": "BLOCKED_BY_ENVIRONMENT" if environment_blocked else "FAIL",
            "error_type": exc.__class__.__name__,
            "error_code": _semantic_failure_code(stage=failure_stage, exc=exc),
            "error_category": category,
            "reason": "configured_model_environment_unavailable" if environment_blocked else "semantic_prototype_certification_failed",
            "case_id": current_case_id,
            "failure_stage": failure_stage,
        }, ensure_ascii=False))
''',
    label="emit bounded semantic failure evidence",
)


inner = ROOT / "services/agent-service/src/agent_core/model_calls/real_model_certification_bundle.py"
replace_once(
    inner,
    '''    return payload


def run_certification_bundle(
''',
    '''    return payload


def _safe_component_failure(component: str, payload: Mapping[str, Any]) -> dict[str, str]:
    safe: dict[str, str] = {
        "component": component,
        "status": str(payload.get("status") or "FAIL"),
        "reason": str(payload.get("reason") or "component_failed"),
        "error_code": str(payload.get("error_code") or "component_failed"),
    }
    for key in ("error_type", "error_category", "case_id", "failure_stage"):
        value = str(payload.get(key) or "").strip()
        if value:
            safe[key] = value
    return safe


def run_certification_bundle(
''',
    label="add safe inner component failure helper",
)
replace_once(
    inner,
    '''        if status == "BLOCKED_BY_ENVIRONMENT":
            return {
                "contract": _BUNDLE_CONTRACT,
                "status": "BLOCKED_BY_ENVIRONMENT",
                "reason": str(payload.get("reason") or "real_model_environment_unavailable"),
                "error_code": str(payload.get("error_code") or "component_environment_blocked"),
                "blocked_component": component,
                "component_launch_count": launched,
            }
        if status != "PASS":
            return {
                "contract": _BUNDLE_CONTRACT,
                "status": "FAIL",
                "reason": "real_model_certification_component_failed",
                "failed_component": component,
                "error_code": str(payload.get("error_code") or "component_failed"),
                "component_launch_count": launched,
            }
''',
    '''        if status == "BLOCKED_BY_ENVIRONMENT":
            failure = _safe_component_failure(component, payload)
            return {
                "contract": _BUNDLE_CONTRACT,
                "status": "BLOCKED_BY_ENVIRONMENT",
                "reason": str(payload.get("reason") or "real_model_environment_unavailable"),
                "error_code": failure["error_code"],
                "blocked_component": component,
                "component_failure": failure,
                "component_launch_count": launched,
            }
        if status != "PASS":
            failure = _safe_component_failure(component, payload)
            return {
                "contract": _BUNDLE_CONTRACT,
                "status": "FAIL",
                "reason": "real_model_certification_component_failed",
                "failed_component": component,
                "error_code": failure["error_code"],
                "component_failure": failure,
                "component_launch_count": launched,
            }
''',
    label="propagate safe inner component failure",
)


outer = ROOT / "scripts/verify_production_certification_bundle.py"
replace_once(
    outer,
    '''                    "error_type",
                    "error_category",
                )
''',
    '''                    "error_type",
                    "error_category",
                    "case_id",
                    "failure_stage",
                )
''',
    label="propagate semantic location through outer boundary",
)


test = ROOT / "services/agent-service/tests/runtime/test_semantic_failure_evidence.py"
test.write_text(
    '''from __future__ import annotations

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
''',
    encoding="utf-8",
)


auto_release = ROOT / ".github/workflows/temporary-auto-release-after-real-model-repair.yml"
auto_release.write_text(
    '''name: temporary-auto-release-after-real-model-repair

"on":
  push:
    branches:
      - main
    paths:
      - .github/workflows/temporary-auto-release-after-real-model-repair.yml
      - services/agent-service/scripts/verify_preprod_conversation_smoke.py
      - services/agent-service/src/agent_core/model_calls/real_model_certification_bundle.py
      - scripts/verify_production_certification_bundle.py

permissions:
  actions: write
  contents: read

concurrency:
  group: temporary-auto-release-after-real-model-repair
  cancel-in-progress: false

jobs:
  dispatch-protected-release:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - name: Dispatch protected production certification
        env:
          GH_TOKEN: ${{ github.token }}
        shell: bash
        run: |
          set -euo pipefail
          jq -n '{
            ref: "main",
            inputs: {
              provider: "deepseek",
              model: "deepseek-v4-flash",
              embedding_model: "text-embedding-v4",
              embedding_dimension: "1024"
            }
          }' | gh api \
            --method POST \
            "repos/${GITHUB_REPOSITORY}/actions/workflows/release.yml/dispatches" \
            --input -
''',
    encoding="utf-8",
)

for relative in (
    ".github/scripts/apply_semantic_failure_evidence.py",
    ".github/workflows/one-time-apply-semantic-failure-evidence.yml",
    ".github/diagnostics/semantic-failure-evidence-trigger.txt",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()
