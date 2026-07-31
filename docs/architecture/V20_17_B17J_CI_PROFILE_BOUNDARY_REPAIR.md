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
