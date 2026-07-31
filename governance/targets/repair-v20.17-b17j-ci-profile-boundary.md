# 目标

- 目标 ID：repair-v20.17-b17j-ci-profile-boundary
- 变更标识：repair-v20.17-b17j-ci-profile-boundary
- 执行上下文：ci
- 目标类型：repair

修复通用项目 `quality` Workflow 无条件运行 Skill-only `project-compatibility-smoke`，导致真实产品候选稳定假红的 Profile 职责错配。

## 允许范围

- `.github/workflows/quality.yml`
- `skill-system/tests/test_ci_profile_boundary.py`
- `docs/architecture/V20_17_B17J_CI_PROFILE_BOUNDARY_REPAIR.md`
- `governance/claims/repair-v20.17-b17j-ci-profile-boundary.json`
- `governance/targets/repair-v20.17-b17j-ci-profile-boundary.md`
- `governance/active-change.json`
- `B17J_STAGE_SUMMARY.json`
- `PHASE_CANDIDATE_NOTICE.md`
- `PHASE_CANDIDATE_MANIFEST.json`
- `release/MANIFEST.json`
- `release/VALIDATION_REPORT.md`
- `README.md`
- `CHANGELOG.md`

## 禁止范围

不得修改客服语义、Prompt、Capability、事务协议、业务规则、数据库实现或 RAG；不得删除或弱化 Skill-only 兼容性 Gate；不得把失败改写为 PASS；不得生成 `production_closed`。

## 验收条件

- 最低质量模式：quick
- 声明清单：`governance/claims/repair-v20.17-b17j-ci-profile-boundary.json`
- 验收 ID：`V20-17-B17J-CI-PROFILE-BOUNDARY-001`

项目 CI 必须运行四个 Skill 自身 Profile，而不运行 Skill-only 产品树兼容性 Gate；Skill-only release 必须继续包含兼容性 Gate。净零差异 GitHub PR 必须越过 Skill 自检 Job，继续进入项目 static/quick Gate。

## 基线

红基线：GitHub Actions run `30607885939`，Job `91084022386`。四个 Skill 自检均 PASS，`project-compatibility-smoke` 因当前产品候选与历史 Skill-only 基线不同而 FAIL，导致后续项目质量 Job 全部无法启动。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：只修复 CI Profile 编排和对应合同测试，不触碰产品运行时。
