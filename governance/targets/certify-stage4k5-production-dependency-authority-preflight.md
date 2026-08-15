# 目标

- 目标 ID：certify-stage4k5-production-dependency-authority-preflight
- 变更标识：portable-certify-stage4k5-production-dependency-authority-preflight
- 执行上下文：ci
- 目标类型：certification

Read-only certify the Stage4K5 production dependency-authority activation prerequisites and fail closed on any missing deployment authority, independent trust roots, signed immutable control material, rollback custody, DB append authority, or exact production verification evidence; do not activate production.

## 允许范围

- 允许变更路径：`services/agent-service/app/services/dependency_authority_composition.py`, `services/agent-service/app/services/readiness_service.py`, `services/agent-service/src/agent_core/runtime/dependency_authority_control.py`, `services/agent-service/src/agent_core/runtime/dependency_authority_persistent_control.py`, `services/agent-service/src/agent_core/runtime/dependency_authority_signed_provider.py`, `services/agent-service/tests/runtime/test_typed_goal_dependency_production_composition.py`
- 新增抽象记录：无

## 禁止范围

Read-only K5-A certification only: no production environment/config mutation, deployment/restart, signing-key creation, signature minting, control/rollback row append, business-state mutation, WP08 dispatch, or production closure; do not modify product source to make the certification pass.

## 验收条件

- 最低质量模式：release
- 声明清单：`governance/claims/certify-stage4k5-production-dependency-authority-preflight.json`
- 验收 ID：`STAGE4K5-AUTHORITY-PREFLIGHT-001`

Current source and release-bound quality evidence must prove the fail-closed Stage4K4 authority boundary. Missing external production authority is a blocking result, never permission to invent identities, keys, signatures, rows, or deployment evidence.

## 基线

Certification is read-only and uses current-source evidence; no product transition baseline or product write is authorized.

## 修复轮次

- 最大轮次：4
- 当前轮次：1
- 失败后：只根据本目标的结构化 Repair Plan 修改唯一 Owner；没有有效进展时停止并重新规划。
