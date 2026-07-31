# 目标

- 目标 ID：repair-v20.17-b17j-ci-profile-boundary
- 变更标识：repair-v20.17-b17j-ci-profile-boundary
- 执行上下文：ci
- 目标类型：repair

修复通用项目 `quality` Workflow 无条件运行 Skill-only `project-compatibility-smoke`，导致真实产品候选稳定假红的 Profile 职责错配。

## 允许范围

- `.github/workflows/quality.yml`
- `skill-system/tests/test_ci_profile_boundary.py`
- `services/agent-service/tests/runtime/test_goal_binding_counterexamples.py`
- `services/agent-service/tests/architecture/test_quality_loop_governance.py`
- `services/agent-service/tests/runtime/test_b17i_production_execution_handoff.py`
- `services/agent-service/tests/runtime/test_b17h_protected_environment_preflight.py`
- `scripts/verify_full_lifecycle_canary.py`
- `services/agent-service/tests/runtime/test_b14f1_sqlite_resource_lifecycle.py`
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
- 验收 ID：`V20-17-B17J-CI-PROFILE-BOUNDARY-001`、`V20-17-B17J-ADVERSARIAL-HARNESS-002`、`V20-17-B17J-CI-ENV-ISOLATION-003`、`V20-17-B17J-VENV-INTERPRETER-004`

项目 CI 必须运行四个 Skill 自身 Profile，而不运行 Skill-only 产品树兼容性 Gate；Skill-only release 必须继续包含兼容性 Gate。净零差异 GitHub PR 必须越过 Skill 自检和 static，并且 Quick 的 adversarial-runtime-counterexamples 不得再因缺失测试助手或已退休 Workflow 步骤而失败。

## 基线

第一红基线：GitHub Actions run `30607885939`，Job `91084022386`。四个 Skill 自检均 PASS，`project-compatibility-smoke` 因当前产品候选与历史 Skill-only 基线不同而 FAIL，导致后续项目质量 Job 全部无法启动。

第二红基线：修复 Profile 边界后的 GitHub Actions run `30608910835` 中，`skill-self-validation` 与 `quality-static` 已 PASS；Quick Job `91087188820` 在 `adversarial-runtime-counterexamples` 暴露三个过期 Harness 断言：两个 B17e 桥接测试调用不存在的 `_load_test_module`，一个架构测试仍要求已经退休的 `Start actual protected-profile services` Workflow 步骤。

第三红基线：GitHub Actions run `30609735023` 中，`adversarial-runtime-counterexamples` 已 `137 passed`，前两轮修复得到验证；标准 `python-test-suites` 在 857 个 Agent 测试中仅剩 3 个失败：B17j Changelog 首段缺失，以及 B17h/B17i 的“无 CI 环境”子进程测试继承了 Runner 的 Workflow/GitHub 环境变量。

第四红基线：GitHub Actions run `30610419110` 中，Skill、Static、`adversarial-runtime-counterexamples`、Agent 857 项、Business 28 项、前端与覆盖率均已通过；唯一新失败是 `full-lifecycle-canary`。Harness 将 `services/agent-service/.venv/bin/python` 调用 `Path.resolve()`，把虚拟环境入口解引用为系统基础 Python，导致确定性模型桩启动时报 `No module named uvicorn`。

## 修复轮次

- 最大轮次：8
- 当前轮次：4
- 第 2 轮：只修复两个过期反例桥接和一个过期发布职责断言，不触碰产品运行时。
- 第 3 轮：补齐 B17j 当前阶段 Changelog，并隔离 B17h/B17i 无 CI 上下文子进程环境，不改变生产脚本失败优先级。
- 第 4 轮：保留选中 Python 虚拟环境入口的符号链接路径，并增加不得解引用为基础解释器的反例；不增删依赖。
