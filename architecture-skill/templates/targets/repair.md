# 目标

- 目标 ID：`change-YYYYMMDD-short-name`
- 变更标识：`working-tree-or-commit`
- 执行上下文：local-change
- 目标类型：repair

已确认根因后的红到绿修复。

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

必须建立失败 Claim 基线，并证明真实范围内变化。

## 修复轮次

- 最大轮次：8
- 当前轮次：1
- 失败后：按 Stop Contract 返回修复、Oracle 审查、重规划、回滚或环境阻断，不得为了推进轮次制造无意义修改。
