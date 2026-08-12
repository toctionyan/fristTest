#!/usr/bin/env python3
"""Create a non-secret, per-run quality target record for CI evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from github_repair_stage3_trusted_projection import project as project_stage3_trusted_judge


CONTROL_ROOT = Path(__file__).resolve().parents[1]

WORKFLOW_MINIMUM_MODE = {
    "quality-static": "static",
    "quality-quick": "quick",
    "quality-integration": "integration",
    "release-quality": "release",
    "governed-ci-repair-stage3": "quick",
}

WORKFLOW_CLAIM_GATES = {
    "quality-static": ["architecture-convergence", "version-consistency"],
    "quality-quick": [
        "adversarial-runtime-counterexamples",
        "python-test-suites",
        "frontend-vitest",
        "coverage-baseline",
        "full-lifecycle-canary",
        "product-browser-journey",
    ],
    "governed-ci-repair-stage3": [
        "adversarial-runtime-counterexamples",
        "python-test-suites",
        "frontend-vitest",
        "coverage-baseline",
        "full-lifecycle-canary",
        "product-browser-journey",
    ],
    "quality-integration": [
        "python-integration-tests", "product-http-smoke",
        "full-lifecycle-canary", "product-browser-journey",
    ],
    "release-quality": [
        "adversarial-runtime-counterexamples",
        "systemic-operational-counterexamples",
        "python-test-suites",
        "frontend-vitest",
        "frontend-build",
        "coverage-baseline",
        "python-integration-tests",
        "product-http-smoke",
        "full-lifecycle-canary",
        "production-certification-bundle",
        "clean-release-preflight",
    ],
}

MODE_RANK = {"static": 0, "quick": 1, "integration": 2, "release": 3}


def _canonical_fingerprint(payload: dict) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_claims(
    workspace: Path, raw_path: str, *, maximum_mode: str
) -> tuple[list[dict], dict]:
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".json":
        raise ValueError("--claims-source must be a safe workspace-relative JSON path")
    source = (workspace / relative).resolve()
    try:
        source.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("--claims-source must stay inside the workspace") from exc
    if not source.is_file():
        raise ValueError(f"--claims-source does not exist: {relative.as_posix()}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    claims = payload.get("claims")
    if payload.get("schema_version") != 1 or not isinstance(claims, list) or not claims:
        raise ValueError("--claims-source must be a non-empty schema_version 1 claim manifest")
    too_strict = [
        str(item.get("id") or "<missing-id>")
        for item in claims
        if MODE_RANK.get(str(item.get("required_mode") or ""), 99) > MODE_RANK[maximum_mode]
    ]
    if too_strict:
        raise ValueError(
            f"source claims exceed {maximum_mode} workflow mode: {', '.join(too_strict)}"
        )
    regression_claims = [
        str(item.get("id") or "<missing-id>")
        for item in claims
        if str(item.get("closure_requirement") or "").strip().lower()
        == "regression-transition"
    ]
    if regression_claims:
        raise ValueError(
            "regression-transition claims require their original failing baseline and "
            "cannot be relabeled as CI current-pass claims: "
            + ", ".join(regression_claims)
        )
    source_target_id = str(payload.get("target_id") or "").strip()
    if not source_target_id:
        raise ValueError("--claims-source must declare target_id")
    metadata = {
        "path": relative.as_posix(),
        "target_id": source_target_id,
        "fingerprint": _canonical_fingerprint(payload),
    }
    for key in ("requirement_catalog", "requirement_profile"):
        if key in payload:
            metadata[key] = payload[key]
    return [dict(item) for item in claims], metadata


def _project_bound_stage3_judge(workspace: Path, target: Path) -> dict:
    """Overlay only current trusted-Judge inputs into the disposable Stage-3 workspace."""
    github_workspace = str(os.getenv("GITHUB_WORKSPACE") or "").strip()
    if github_workspace:
        evidence_path = (
            Path(github_workspace).expanduser().resolve()
            / "stage3-evidence"
            / "trusted-judge-projection.json"
        )
    else:
        evidence_path = target.with_suffix(".trusted-judge-projection.json")
    payload = project_stage3_trusted_judge(
        candidate_root=workspace,
        judge_root=CONTROL_ROOT,
        output_path=evidence_path,
    )
    return {
        "schema": payload["schema"],
        "judge_manifest_sha256": payload["judge_manifest_sha256"],
        "projected_file_count": payload["projected_file_count"],
        "repair_patch_changed": False,
        "candidate_commit_changed": False,
        "publication_authority_changed": False,
        "production_closed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--claims-source")
    args = parser.parse_args()
    minimum_mode = WORKFLOW_MINIMUM_MODE.get(args.workflow)
    if minimum_mode is None:
        parser.error(f"unsupported quality workflow: {args.workflow}")
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    workspace = Path.cwd().resolve()
    claims = target.with_suffix(".claims.json")
    try:
        claims_relative = claims.relative_to(workspace).as_posix()
    except ValueError as exc:
        parser.error(f"CI target output must stay inside the workspace: {exc}")
    source_metadata: dict | None = None
    if args.claims_source:
        try:
            claim_rows, source_metadata = _source_claims(
                workspace, args.claims_source, maximum_mode=minimum_mode
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
    else:
        claim_id = f"CI-{args.workflow.upper().replace('-', '_')}-001"
        risk = "P1" if minimum_mode in {"integration", "release"} else "P2"
        evidence_kind = (
            "release-provenance"
            if minimum_mode == "release"
            else "integration"
            if minimum_mode == "integration"
            else "counterexample"
            if minimum_mode == "quick"
            else "static-contract"
        )
        claim_rows = [
            {
                "id": claim_id,
                "statement": f"Immutable commit {args.ref} satisfies the complete {args.workflow} gate contract.",
                "risk": risk,
                "required_mode": minimum_mode,
                "evidence_kind": evidence_kind,
                "required_gates": WORKFLOW_CLAIM_GATES[args.workflow],
                "evidence_refs": [
                    f"gate-log:{gate_id}"
                    for gate_id in WORKFLOW_CLAIM_GATES[args.workflow]
                ],
                "owner": "ci-quality-workflow",
                "closure_requirement": "current-pass",
            }
        ]

    trusted_judge_projection: dict | None = None
    if args.workflow == "governed-ci-repair-stage3":
        try:
            trusted_judge_projection = _project_bound_stage3_judge(workspace, target)
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            parser.error(f"Stage-3 trusted Judge projection failed: {exc}")

    generated_manifest = {
        "schema_version": 1,
        "target_id": f"ci:{args.workflow}:{args.ref}",
        "claims": claim_rows,
    }
    if source_metadata is not None:
        generated_manifest["source_claim_manifest"] = source_metadata
        for key in ("requirement_catalog", "requirement_profile"):
            if key in source_metadata:
                generated_manifest[key] = source_metadata[key]
    if trusted_judge_projection is not None:
        generated_manifest["trusted_judge_projection"] = trusted_judge_projection
    claims.write_text(
        json.dumps(
            generated_manifest,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    target.write_text(
        f"""# 目标

- 目标 ID：ci:{args.workflow}:{args.ref}
- 变更标识：{args.ref}
- 执行上下文：ci
- 目标类型：certification

验证 CI 变更 {args.ref} 在 {args.workflow} 中满足已选择的发布级质量 Gate。

## 允许范围

只执行当前提交的声明式质量策略和已锁定依赖。

- 允许变更路径：**
- 新增抽象记录：ci-not-applicable

## 禁止范围

不修改源码、不访问生产业务数据、不将 CI 密钥写入 evidence。

## 验收条件

所有已选择 Gate 通过；缺失环境必须阻断并上传 repair plan。

- 最低质量模式：{minimum_mode}
- 声明清单：{claims_relative}
- 验收 ID：{', '.join(str(item['id']) for item in claim_rows)}

## 基线

CI 验证当前不可变提交；本地变更前 baseline 由开发者的 local-change target evidence 单独保留。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：根据 repair-plan.json 的 Owner 与 rerun 命令做最小修复；CI 不在同一 runner 内自动修改或重复执行命令。
""",
        encoding="utf-8",
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
