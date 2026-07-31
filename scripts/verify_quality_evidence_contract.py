#!/usr/bin/env python3
"""Verify that the declared quality-evidence schema matches controller output.

This is intentionally dependency-free: the static gate must be able to catch a
schema/controller drift before any test environment is installed.  A tiny CI
target and policy are executed in a temporary directory, so the check proves
the actual `run-summary.json` shape without modifying the workspace.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any


def _controller(workspace: Path) -> Any:
    path = workspace / "scripts" / "quality_loop.py"
    spec = importlib.util.spec_from_file_location("quality_evidence_controller", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load quality controller: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _target() -> str:
    return """# 目标

- 目标 ID：quality-evidence-contract-ci
- 变更标识：quality-evidence-contract-ref
- 执行上下文：ci
- 目标类型：certification

验证控制器输出与已提交 evidence schema 一致。

## 允许范围

- 允许变更路径：**
- 新增抽象记录：ci-not-applicable

## 禁止范围

不修改工作区。

## 验收条件

生成的 run summary 满足 evidence schema。

- 最低质量模式：static
- 声明清单：governance/claims/quality-evidence-contract.json
- 验收 ID：QUALITY-EVIDENCE-001

## 基线

CI 使用不可变输入，不伪造本地 baseline。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：使用 repair plan。
"""


def _policy() -> dict[str, Any]:
    return {
        "version": "contract-test",
        "steps": [
            {
                "id": "contract-ast",
                "name": "contract-ast",
                "modes": ["static"],
                "kind": "python_ast_parse",
                "owner": "quality-controller",
                "category": "syntax",
                "blocking_level": "required",
                "repair_playbook": "repair the temporary contract fixture",
                "rerun_contract": "dependency_closure_then_downstream",
                "depends_on": [],
                "environment": {},
                "timeout_seconds": 10,
            }
        ],
    }


def verify(workspace: Path) -> dict[str, Any]:
    errors: list[str] = []
    controller = _controller(workspace)
    schema_path = workspace / "governance" / "evidence_schema" / "quality-loop-evidence.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "errors": [f"cannot read evidence schema: {exc}"]}
    required = set(schema.get("required") or [])
    expected = set(controller.EVIDENCE_REQUIRED_FIELDS)
    if schema.get("properties", {}).get("schema_version", {}).get("const") != controller.EVIDENCE_SCHEMA_VERSION:
        errors.append("evidence schema version does not match scripts/quality_loop.py")
    if required != expected:
        errors.append("evidence schema required fields do not match controller contract")
    result_schema = schema.get("properties", {}).get("results", {}).get("items", {})
    if not {"id", "status", "owner", "category", "stdout", "stderr"}.issubset(set(result_schema.get("required") or [])):
        errors.append("evidence schema result rows lack repair-plan evidence fields")

    with tempfile.TemporaryDirectory(prefix="quality-evidence-contract-") as directory:
        temporary = Path(directory)
        (temporary / "governance").mkdir()
        (temporary / "VERSION").write_text("contract-test\n", encoding="utf-8")
        policy_path = temporary / "governance" / "policy.json"
        policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
        target_path = temporary / "target.md"
        target_path.write_text(_target(), encoding="utf-8")
        claims_path = temporary / "governance" / "claims" / "quality-evidence-contract.json"
        claims_path.parent.mkdir(parents=True, exist_ok=True)
        schema_fixture = temporary / "governance" / "evidence_schema" / "quality-loop-evidence.schema.json"
        schema_fixture.parent.mkdir(parents=True, exist_ok=True)
        schema_fixture.write_text(
            json.dumps({"schema_version": controller.EVIDENCE_SCHEMA_VERSION}),
            encoding="utf-8",
        )
        claims_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "target_id": "quality-evidence-contract-ci",
                    "claims": [
                        {
                            "id": "QUALITY-EVIDENCE-001",
                            "statement": "Controller evidence matches the committed evidence schema.",
                            "risk": "P2",
                            "required_mode": "static",
                            "evidence_kind": "static-contract",
                            "required_gates": ["contract-ast"],
                            "evidence_refs": ["governance/evidence_schema/quality-loop-evidence.schema.json"],
                            "owner": "quality-controller",
                            "closure_requirement": "current-pass",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        evidence_dir = temporary / "evidence"
        summary = controller.run_loop(
            temporary,
            policy_path,
            mode="static",
            evidence_dir=evidence_dir,
            rerun_from=None,
            target_path=target_path,
            baseline=False,
            baseline_evidence=None,
            prior_evidence=None,
            state_dir=temporary / "state",
        )
        persisted = json.loads((evidence_dir / "run-summary.json").read_text(encoding="utf-8"))
        if summary != persisted:
            errors.append("persisted run-summary.json differs from controller return value")
        if summary.get("schema_version") != controller.EVIDENCE_SCHEMA_VERSION:
            errors.append("actual controller evidence uses an unexpected schema version")
        missing = expected - set(summary)
        if missing:
            errors.append("actual controller evidence misses required fields: " + ", ".join(sorted(missing)))
        if summary.get("decision") != controller.PASS or not (evidence_dir / "repair-plan.json").is_file():
            errors.append("temporary controller run did not produce a PASS summary and repair-plan.json")
    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "schema_version": controller.EVIDENCE_SCHEMA_VERSION,
        "required_fields": sorted(expected),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-root", default=".")
    args = parser.parse_args()
    result = verify(Path(args.workspace_root).resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
