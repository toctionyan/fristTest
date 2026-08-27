from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "product_target_bound_ci.py"
SPEC = importlib.util.spec_from_file_location("product_target_bound_ci", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_stage2b1_baseline_reset_keeps_governance_and_resets_only_allowed_product_paths(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    base = tmp_path / "base"
    baseline = tmp_path / "baseline"
    for root in (candidate, base):
        (root / "governance/targets").mkdir(parents=True)
        (root / "governance/claims").mkdir(parents=True)
        (root / "services/agent-service/src").mkdir(parents=True)
    target = """# 目标\n\n- 目标 ID：change-001\n- 变更标识：portable-change-001\n- 执行上下文：local-change\n- 目标类型：migration\n\n## 允许范围\n\n- 允许变更路径：`services/agent-service/src/example.py`\n- 新增抽象记录：无\n\n## 禁止范围\n\nno\n\n## 验收条件\n\n- 最低质量模式：quick\n- 声明清单：`governance/claims/change-001.json`\n- 验收 ID：CHANGE-001\n\nacceptance\n\n## 基线\n\nbaseline\n\n## 修复轮次\n\n- 最大轮次：2\n- 当前轮次：1\n"""
    claim = {"schema_version": 1, "target_id": "change-001", "claims": [{"id": "CHANGE-001", "statement": "x", "risk": "P1", "required_mode": "quick", "evidence_kind": "counterexample", "required_gates": ["python-test-suites"], "evidence_refs": ["gate-log:python-test-suites"], "owner": "x", "closure_requirement": "regression-transition"}]}
    for root in (candidate, base):
        (root / "governance/targets/change-001.md").write_text(target, encoding="utf-8")
        (root / "governance/claims/change-001.json").write_text(json.dumps(claim), encoding="utf-8")
    contract = {"profile": "product-code", "change_id": "change-001", "quality_target": "governance/targets/change-001.md", "allowed_paths": ["services/agent-service/src/example.py"], "minimum_quality_mode": "quick"}
    (candidate / "governance/active-change.json").write_text(json.dumps(contract), encoding="utf-8")
    (base / "governance/active-change.json").write_text(json.dumps(contract), encoding="utf-8")
    (base / "services/agent-service/src/example.py").write_text("before\n", encoding="utf-8")
    (candidate / "services/agent-service/src/example.py").write_text("after\n", encoding="utf-8")
    (candidate / "governance/marker.txt").write_text("candidate governance\n", encoding="utf-8")

    output = tmp_path / "binding.json"
    result = MODULE.prepare_baseline(str(candidate), str(base), str(baseline), str(output), "a" * 40, "b" * 40)

    assert result["target_identity"]["id"] == "change-001"
    assert (baseline / "services/agent-service/src/example.py").read_text(encoding="utf-8") == "before\n"
    assert (baseline / "governance/marker.txt").read_text(encoding="utf-8") == "candidate governance\n"
