# 目标

- 目标 ID：`change-YYYYMMDD-short-name`
- 变更标识：`working-tree-or-commit`
- 执行上下文：local-change
- 目标类型：oracle-review

审查 Claim、Requirement 与测试 Oracle。

## 允许范围

- 允许变更路径：`architecture-skill/**`, `skill-system/**`, `governance/**`, `scripts/**`, `.agents/**`, `.claude/**`, `.codex/**`, `AGENTS.md`, `CLAUDE.md`
- 新增抽象记录：无

## 禁止范围

- `services/**`
- `web/**`
- `contracts/**`

## 验收条件

- 最低质量模式：static
- 声明清单：`governance/claims/change-YYYYMMDD-short-name.json`
- 验收 ID：`CLAIM-001`

## 基线

记录当前差距或 current-pass 证据；不强制制造源码变化。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：按 Stop Contract 返回修复、Oracle 审查、重规划、回滚或环境阻断，不得为了推进轮次制造无意义修改。
