# V20.17 B17j — CI Profile Boundary Repair

## 真实红基线

GitHub Actions run `30607885939` 在零文件差异 PR 上执行正式 `quality` Workflow。
`skill-static`、`skill-unit`、`skill-host-integration` 和 `skill-security` 全部通过；唯一失败来自 `project-compatibility-smoke`。

该 Gate 的合同是“证明一次 Skill-only 改造没有修改客服产品源码”。它将当前产品候选与历史 Skill-only 基线逐字节比较，因此不能作为通用项目 CI 的前置条件。B17a–B17i 已获批修改生产认证 Harness，继续在项目 CI 中无条件运行该 Gate 会稳定制造假红。

## 唯一职责修复

- 项目级 `.github/workflows/quality.yml` 只运行四个 Skill 自身检查：static、unit、host integration、security。
- `project-compatibility-smoke` 继续保留在 `skill-control-plane`。
- `skill-release` 继续包含 `skill-control-plane`，所以 Skill-only 发布仍必须证明产品源码未被修改。
- 产品 Quality Loop 的 static/quick/integration/release Gate 不降低、不跳过。

## 禁止的错误修复

- 不更新历史 Skill-only 产品基线来掩盖产品变化。
- 不把兼容性失败改写为 PASS。
- 不删除 `project-compatibility-smoke`。
- 不减少产品 Quality Loop、真实模型或生产认证 Gate。

## 验证

`test_ci_profile_boundary.py` 同时证明项目 CI 不再调用 Skill-only 兼容性 Gate，并证明 Skill-only 发布链仍保留该 Gate。

## 第 2 轮：真实 Quick Harness 收敛

GitHub run `30608910835` 已证明新的 Profile 边界正确：Skill 自检与 static 均 PASS。Quick 的唯一红项位于 `adversarial-runtime-counterexamples`：两个桥接测试引用不存在的加载助手，一个测试仍把受保护服务启动职责错误地放在 GitHub Workflow。

修复保持唯一权威：B17e 反例通过 Python 直接 import 调用原始权威测试；发布职责测试证明 Workflow 只委托 `run_production_release.py` 和 `verify_production_certification_bundle.py`，而真正的 preprod 服务环境由 `verify_full_lifecycle_canary.py` 动态拥有。没有把旧步骤加回 Workflow，也没有降低反例。

## 第 3 轮：Runner 环境隔离

GitHub run `30609735023` 证明第 2 轮三个反例已全部修复（`137 passed`）。标准全量测试剩余的三个失败中，一个是 B17j Changelog 首段未同步，两个是测试场景污染：声明“无 CI 上下文”的子进程继承了父 GitHub Runner 的环境。

正确修复是在测试 Harness 构造明确的隔离环境，而不是修改生产 Preflight/Admission 的错误优先级。这样真实 Workflow 仍按 fail-closed 顺序裁决，离线反例也能稳定复现目标状态。
